"""設定端點（LLM/QC 配置讀寫 + 即時測試 + QC DB 連線測試）；全路徑自帶 /api。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core import auth, config, db  # noqa: F401  config import 觸發 .env 載入
from app.judge.llm import client as llm_client

router = APIRouter()


def load_user_context(user: dict = Depends(auth.get_current_user)) -> dict:
    """認證守衛（settings / datasource 端點共用）。

    註：contextvar 須在 handler「同一 threadpool thread」內設定才對 judge 路徑可見；
    FastAPI sync dependency 與 sync endpoint 可能跑在不同 thread，故 set_current 改由
    各 handler 內呼叫 _activate_settings，不在此 Depends 設（跨 thread 不可見）。
    """
    return user


def _activate_settings(user_id: str) -> None:
    """在 handler 內注入該 user 設定到 contextvar（同 thread，judge 路徑 llm client 才讀得到）。"""
    from app.core import settings as app_settings

    s = app_settings.load_settings(user_id)
    # judge 路徑讀 contextvar：注入「active LLM config + provider_tokens」組出的 effective flat dict
    # （client._resolve 所讀 key 不變 → client.py 零改動）
    app_settings.set_current(app_settings.effective_llm_dict(s))


class LlmConfigIn(BaseModel):
    """單套 LLM config（機密 token 不在此，走共用 provider_tokens）。id 新建留空，後端補 uuid。"""

    id: str | None = None
    label: str = "未命名配置"
    provider: str | None = None
    base_url: str | None = None
    model: str | None = None
    temperature: float | None = None
    thinking: str | None = None
    reasoning_effort: str | None = None


class QcConfigIn(BaseModel):
    """單套 QC DB config。password 為 transient 欄位：後端抽出存 qc_passwords[id]，不落 config 本體。"""

    id: str | None = None
    label: str = "未命名連線"
    env: str | None = None
    host: str | None = None
    port: int | None = None
    user: str | None = None
    names: list[str] = []
    schemas: list[str] = []
    password: str | None = None  # transient；空/遮罩不覆蓋既有


class SettingsIn(BaseModel):
    """設定部分/整包 patch（皆選填，缺省欄位不動）。機密空/遮罩值後端不覆蓋既有。"""

    # LLM 多 config
    llm_configs: list[LlmConfigIn] | None = None
    active_llm_config_id: str | None = None
    llm_tokens: dict | None = None  # { config_id: token } per-config 機密（每套配置各自獨立）
    provider_tokens: dict | None = None  # 舊 per-provider 共用（保留相容，resolution 已不用）
    provider_models: dict | None = None  # 各供應商自訂 model 清單
    # QC DB 多 config
    qc_configs: list[QcConfigIn] | None = None
    active_qc_config_id: str | None = None
    # 概覽自訂看板（非機密）
    overview_boards: list[dict] | None = None  # [{id,label,chartIds[]}]
    active_overview_board_id: str | None = None
    # 導出偏好（非機密）：Google Drive 上傳資料夾 URL；傳空字串＝清除（endpoint exclude_none，None 不動）
    gdrive_upload_folder_url: str | None = None


@router.get("/api/settings")
def get_settings(user: dict = Depends(load_user_context)) -> dict:
    """當前 user 的 LLM 模型配置（api_token 遮罩，附 has_token / stub_mode）。"""
    from app.core import settings as app_settings

    _activate_settings(user["user_id"])
    data = app_settings.masked(user["user_id"])
    data["stub_mode"] = llm_client.is_stub()
    return data


@router.post("/api/settings")
def update_settings(body: SettingsIn, user: dict = Depends(load_user_context)) -> dict:
    """更新當前 user 的模型配置（空/遮罩 token 不覆蓋既有）。"""
    from app.core import settings as app_settings

    data = app_settings.save_settings(user["user_id"], body.model_dump(exclude_none=True))
    _activate_settings(user["user_id"])  # 反映新 token（stub_mode）
    data["stub_mode"] = llm_client.is_stub()
    return data


@router.get("/api/settings/raw")
def get_settings_raw(user: dict = Depends(load_user_context)) -> dict:
    """當前 user 的完整配置（api_token 明文）——供設定面板眼睛切換顯示全文。

    ⚠️ 明文回傳 token：僅限受信任的本地 / 內網環境，勿暴露於公網。
    """
    from app.core import settings as app_settings

    _activate_settings(user["user_id"])
    data = app_settings.raw(user["user_id"])
    data["stub_mode"] = llm_client.is_stub()
    return data


class TestLlmIn(BaseModel):
    """即時測試 LLM 入參：當前表單 flat 值（非已儲存）；token 空/遮罩沿用已儲存該 config token。"""

    base_url: str | None = None
    model: str | None = None
    temperature: float | None = None
    # default | on | off（per-provider 傳遞邏輯見 client._reasoning_kwargs）
    thinking: str | None = None
    reasoning_effort: str | None = None
    api_token: str | None = (
        None  # 該配置表單明文 token（空/遮罩 → 沿用已儲存 llm_tokens[config_id]）
    )
    config_id: str | None = None  # 反查已儲存該套 token 用（api_token 空/遮罩時）


@router.post("/api/settings/test-llm")
def test_llm(body: TestLlmIn, user: dict = Depends(load_user_context)) -> dict:
    """即時測試 LLM 連線：用「當前表單值」（body，非已儲存）送極短 prompt，**不寫入** user_settings。

    token 為空 / 遮罩時沿用已儲存該 config（config_id）明文 llm_tokens（per-config，免重輸）。
    """
    from app.core import settings as app_settings

    saved = app_settings.load_settings(user["user_id"])  # 含明文 llm_tokens
    # per-config token：表單明文優先；空/遮罩 → 沿用已儲存該套 config 的 llm_tokens[config_id]
    form_tok = body.api_token
    if form_tok and "***" not in str(form_tok) and "…" not in str(form_tok):
        token = form_tok
    else:
        # 空/遮罩 → 沿用已儲存該套 token；仍無則走 provider-aware env 後備（僅 OpenAI），
        # 與 judge 路徑共用 resolve_provider_token 一份判定，避免非 OpenAI 配置誤拿 OpenAI key 測連線
        token = app_settings.resolve_provider_token(
            {
                "api_token": (saved.get("llm_tokens") or {}).get(body.config_id or ""),
                "base_url": (body.base_url or "").strip(),
            }
        )
    cfg = {
        "token": token,
        "base_url": (body.base_url or "").strip(),
        "model": body.model or config.env.ai_judge_model,
        "temperature": body.temperature,
        "thinking": body.thinking or "default",
        "reasoning_effort": body.reasoning_effort or "default",
    }
    return llm_client.ping(cfg=cfg)


class QcDbTestIn(BaseModel):
    """測試連線入參（皆選填）；config_id 指定要測哪套（預設 active），空/遮罩 password 沿用既存明文。"""

    config_id: str | None = None  # 反查 qc_passwords 用；None → active_qc_config_id
    env: str | None = None
    host: str | None = None
    port: int | None = None
    names: list[str] | None = None
    schemas: list[str] | None = None
    user: str | None = None
    password: str | None = None


def _qc_db_bootstrap_name(cfg: dict) -> str:
    """決定測試連線/列舉 database 用的 bootstrap dbname。

    一律用 env 的預設起手庫（postgres——必存在、不受 QC 實例輪替影響），不測具體綁定庫：
    綁定庫可能已隨實例輪替下線（如 qc201），以其起手會讓「測試連線」本身失敗，
    使用者反而無法重載清單改綁（死循環）。PostgreSQL 連任一庫即可 SELECT pg_database 列出全部；
    綁定庫是否仍存在改由測試結果的 stale_names 標注。
    """
    from app.core.settings import qc_db_env_name

    return qc_db_env_name(cfg.get("env"))


def _try_qc_db_connect(cfg: dict) -> dict:
    """以 cfg 連 QC DB（5s timeout）並列舉可用 database / schema，回 {ok, databases?, schemas?, error?}。

    不回傳含密碼的連線字串。連線成功後 SELECT pg_database（排除 template）+ information_schema.schemata
    （排除系統 schema）供前端多選載入。schema 為連線「起手庫」的清單（schema 屬 per-database，
    多選多庫時以起手庫為準；KKday 多數庫為 public，實務差異小）。
    """
    host = cfg.get("host") or ""
    if not host:
        return {"ok": False, "error": "未設定 host"}
    name = _qc_db_bootstrap_name(cfg)
    if not name:
        # 防呆：libpq 在 dbname 空時會預設用 username 當 database，產生誤導性 "database <user> does not exist"
        return {"ok": False, "error": "未設定 database name（請先選擇環境或輸入起手庫）"}
    try:
        import psycopg2  # 延遲匯入：未裝時不阻斷面板儲存
    except ImportError:
        return {
            "ok": False,
            "error": "後端未安裝 psycopg2，無法測試連線（pip install psycopg2-binary）",
        }
    from app.core.settings import (
        QC_DB_DEFAULTS,  # port fallback 取共用 config/global/qc_db.json
    )

    try:
        conn = psycopg2.connect(
            host=host,
            port=cfg.get("port") or QC_DB_DEFAULTS["port"],
            dbname=name,
            user=cfg.get("user") or "",
            password=cfg.get("password") or "",
            connect_timeout=config.env.qc_db_connect_timeout,
        )
        try:
            with conn.cursor() as cur:
                # 列舉非 template 的可連 database，供前端多選；排序穩定便於閱讀
                cur.execute(
                    "SELECT datname FROM pg_database "
                    "WHERE datistemplate = false AND datallowconn = true "
                    "ORDER BY datname"
                )
                databases = [r[0] for r in cur.fetchall()]
                # 列舉起手庫的使用者 schema（排除 pg_* / information_schema 系統 schema）
                cur.execute(
                    "SELECT schema_name FROM information_schema.schemata "
                    "WHERE schema_name NOT LIKE 'pg\\_%' "
                    "AND schema_name <> 'information_schema' "
                    "ORDER BY schema_name"
                )
                schemas = [r[0] for r in cur.fetchall()]
        finally:
            conn.close()
        # 綁定庫健檢：已綁定但已不存在的庫（如 QC 實例輪替下線）標注回前端，供改綁提示
        stale = [n for n in (cfg.get("names") or []) if n not in databases]
        return {"ok": True, "databases": databases, "schemas": schemas, "stale_names": stale}
    except Exception as e:  # 只回錯誤首行（避免洩漏連線細節 / 密碼）
        return {"ok": False, "error": str(e).splitlines()[0][:200]}


@router.post("/api/datasource/qc-db/test")
def test_qc_db(body: QcDbTestIn, user: dict = Depends(load_user_context)) -> dict:
    """測試某套 QC DB 連線並列舉 database/schema：以指定 config（或 active）為底，body 覆蓋表單值。

    password 空/遮罩 → 反查 qc_passwords[config_id]（免重輸）；config_id 缺省取 active_qc_config_id。
    """
    from app.core import settings as app_settings

    saved = app_settings.load_settings(user["user_id"])
    cid = body.config_id or saved.get("active_qc_config_id")
    base = next((c for c in (saved.get("qc_configs") or []) if c.get("id") == cid), {})
    cfg = {k: base.get(k) for k in ("env", "host", "port", "user", "names", "schemas")}
    # body 表單值覆蓋（None 不覆蓋）
    for k in ("env", "host", "port", "user", "names", "schemas"):
        v = getattr(body, k, None)
        if v is not None:
            cfg[k] = v
    # password：body 明文優先；空/遮罩 → 沿用 qc_passwords[cid]
    pw = body.password
    if pw and "***" not in pw and "…" not in pw:
        cfg["password"] = pw
    else:
        cfg["password"] = (saved.get("qc_passwords") or {}).get(cid, "")
    return _try_qc_db_connect(cfg)

"""設定持久化（per-user，存 DB user_settings 表）——多套 config + 啟用切換模型。

結構（user_settings.data JSON）：
- LLM：`llm_configs[]`（每套 {id,label,provider,base_url,model,temperature,thinking,reasoning_effort}）
  + `active_llm_config_id`；token 不入 config，存跨 config 共用的 `provider_tokens`（per-provider 機密）；
  `provider_models`（各供應商自訂 model 清單）。
- QC DB：`qc_configs[]`（每套 {id,label,env,host,port,user,names[],schemas[]}）+ `active_qc_config_id`；
  password 不入 config，存 `qc_passwords`（per-config 機密，key=config_id）。
- `taxonomy_overrides`（規整 L1/L2/L3，不變）。

機密絕不明文回前端：masked() 逐 key 遮罩 provider_tokens / qc_passwords；raw() 供「眼睛顯示全文」與編輯回填。
空/遮罩值 save 不覆蓋既有機密。舊單一 active flat 格式由 load_settings 偵測並自動遷移 + 持久化（穩定 uuid）。

judge 路徑（llm client）不傳 user_id，而是透過 contextvar `current()` 取「端點注入的 effective 設定」
（effective_llm_dict 由 active LLM config + provider_tokens 組出）——故 client.py 零改動。
"""

from __future__ import annotations

import contextvars
import json
import uuid
from pathlib import Path

from app.core import db

# 跨語言共用的「非機密」全局預設值，按領域拆檔置於 repo 根 config/global/（前端 @config/global/* 同讀）。
# parents[3]：settings.py = backend/app/core/settings.py → repo 根。後續新增全局配置於此目錄各建一 JSON。
_GLOBAL_DIR = Path(__file__).resolve().parents[3] / "config" / "global"
# QC DB 連線預設（port/schema/defaultEnv/environments）；main.py 連線測試的 port fallback 亦取此。
QC_DB_DEFAULTS: dict = json.loads((_GLOBAL_DIR / "default_qc.json").read_text(encoding="utf-8"))
_LLM_DEFAULTS: dict = json.loads((_GLOBAL_DIR / "default_llm.json").read_text(encoding="utf-8"))
# LLM model 下拉的最低版本門檻（僅 gpt-* 受限）；/api/settings/models 動態清單過濾用。
LLM_MODEL_MIN_VERSION: str = _LLM_DEFAULTS.get("modelMinVersion", "5.4")
# LLM 供應商目錄（id/base_url/defaultModels）；model 下拉清單 SSOT，list_models() 讀此（不打 /v1/models）。
LLM_PROVIDERS: list = _LLM_DEFAULTS.get("providers", [])

def qc_db_env_name(env_id: str | None) -> str:
    """回某 QC DB 環境（sit/stage）的 bootstrap database 名（測試連線/列舉 database 的起手庫）。

    未知 env_id 回退 defaultEnv 的；環境表為空回空字串。供 main.py 測試連線決定 bootstrap dbname。
    """
    envs = QC_DB_DEFAULTS.get("environments", [])
    target = env_id or QC_DB_DEFAULTS.get("defaultEnv")
    hit = next((e for e in envs if e.get("id") == target), None)
    if not hit and envs:
        dflt = QC_DB_DEFAULTS.get("defaultEnv")
        hit = next((e for e in envs if e.get("id") == dflt), envs[0])
    return str(hit.get("name", "")) if hit else ""


def provider_id_for(base_url: str) -> str:
    """由 base_url 反推 provider id（openai/gemini/bytedance），與前端 deriveProviderId 對齊。

    provider_tokens 以此為 key；judge 路徑 _resolve 亦以此取當前 provider 的 token。
    自訂 / 未知 base_url 一律歸 openai（OpenAI 相容端點為大宗）。
    """
    base = (base_url or "").strip()
    hit = next((p for p in LLM_PROVIDERS if p.get("base_url") == base), None)
    if hit:
        return str(hit["id"])
    if "generativelanguage" in base:
        return "gemini"
    if "bytepluses" in base or "volces" in base:
        return "bytedance"
    return "openai"


def _mask_secret(tok: str) -> str:
    """機密遮罩：>12 字顯示前 7 + … + 後 4；短值顯 ***；空值顯空字串。"""
    tok = tok or ""
    return (tok[:7] + "…" + tok[-4:]) if len(tok) > 12 else ("***" if tok else "")


def _is_masked(v: object) -> bool:
    """是否為遮罩值（含 *** 或 …）；用於 save 時判斷「不覆蓋既有機密」。"""
    s = str(v or "")
    return "***" in s or "…" in s


def _ensure_id(cfg: dict) -> dict:
    """補 uuid id（前端新建留空時）；回新 dict，不就地改入參。"""
    if cfg.get("id"):
        return dict(cfg)
    return {**cfg, "id": str(uuid.uuid4())}


# 單套 LLM config 的非機密預設：新建 config 的底，effective_llm_dict 在 active 失效時亦回退至此。
_DEFAULT_LLM: dict = {
    "provider": "openai",  # openai | gemini | bytedance | custom
    "base_url": "",  # 空＝OpenAI 預設端點
    "model": "gpt-5-nano",  # 對齊 config/global/default_llm.json defaultModel（最省；準確度需驗，見 memory）
    "temperature": None,  # None＝用 API 預設（gpt-5 系列鎖定不送）
    "thinking": "default",  # default | on | off
    "reasoning_effort": "default",  # default | none | low | medium | high | xhigh
}

# 多 config 結構的 key 模板（值僅作型別樣板；實際以 _blank_settings() 產深複本）。
# llm_configs[]：每套 {id,label, + _DEFAULT_LLM 欄位}；token 不入 config，存共用 provider_tokens。
# qc_configs[]：每套 {id,label,env,host,port,user,names[],schemas[]}；password 不入 config，存 qc_passwords[id]。
_NEW_DEFAULT: dict = {
    "llm_configs": [],
    "active_llm_config_id": None,
    "provider_tokens": {},  # { provider_id: token } 跨 config 共用（per-provider 機密）
    "provider_models": {},  # { provider_id: [model_id...] } 各供應商自訂 model 清單
    "qc_configs": [],
    "active_qc_config_id": None,
    "qc_passwords": {},  # { config_id: password } per-config 機密
    "taxonomy_overrides": {},  # 規整 L1/L2/L3 sparse map（不變）
    "overview_boards": [],  # 概覽自訂組合看板 [{id,label,chartIds[]}]（非機密）
    "active_overview_board_id": None,
}

# 舊 flat 格式的指紋 key：load 時偵測到即觸發 _migrate_legacy。
_LEGACY_KEYS = frozenset(
    {"provider", "model", "base_url", "api_token", "qc_db_env", "qc_db_host", "qc_db_name"}
)

# 當前 request 生效的 user 設定（端點 handler 注入）；judge 路徑經 current() 讀取。
# 注入值為 effective_llm_dict() 組出的 flat dict（保留 client._resolve 所讀的 key）。
_current: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "current_settings", default=None
)


def _blank_settings() -> dict:
    """全新空白設定（深複本，避免共用 mutable 預設）。"""
    return {
        "llm_configs": [],
        "active_llm_config_id": None,
        "provider_tokens": {},
        "provider_models": {},
        "qc_configs": [],
        "active_qc_config_id": None,
        "qc_passwords": {},
        "taxonomy_overrides": {},
        "overview_boards": [],
        "active_overview_board_id": None,
    }


def _is_legacy_format(data: dict) -> bool:
    """偵測舊單一 active 配置 flat 格式（有任一 legacy 指紋 key）。"""
    return bool(_LEGACY_KEYS & set(data.keys()))


def _migrate_legacy(data: dict) -> dict:
    """舊 flat dict → 新多 config 結構：LLM/QC 各轉成第一套並設為 active，機密歸入對應 map。

    沿用既有 legacy 規則：api_token→provider_tokens、qc_db_name→names、qc_db_schema→schemas。
    """
    new = _blank_settings()

    # provider_tokens：保留既有 + 舊單值 api_token 歸入（不覆蓋既有）
    ptokens = dict(data.get("provider_tokens") or {})
    legacy_token = data.get("api_token")
    if legacy_token:
        ptokens.setdefault(provider_id_for(data.get("base_url", "")), legacy_token)
    new["provider_tokens"] = ptokens
    new["provider_models"] = dict(data.get("provider_models") or {})

    # LLM：舊單一 active config → llm_configs[0]
    llm_id = str(uuid.uuid4())
    new["llm_configs"] = [
        {
            "id": llm_id,
            "label": "預設配置（自動遷移）",
            "provider": data.get("provider", _DEFAULT_LLM["provider"]),
            "base_url": data.get("base_url", ""),
            "model": data.get("model", _DEFAULT_LLM["model"]),
            "temperature": data.get("temperature"),
            "thinking": data.get("thinking", "default"),
            "reasoning_effort": data.get("reasoning_effort", "default"),
        }
    ]
    new["active_llm_config_id"] = llm_id

    # QC：names/schemas 由舊單值或陣列遷移
    names = list(data.get("qc_db_names") or [])
    if not names and data.get("qc_db_name"):
        names = [data["qc_db_name"]]
    schemas = list(data.get("qc_db_schemas") or [])
    if not schemas and data.get("qc_db_schema"):
        schemas = [data["qc_db_schema"]]
    if not schemas:
        schemas = [QC_DB_DEFAULTS["schema"]]
    # 僅當舊資料有 QC 連線痕跡才建 config（host / names / user 任一）
    if data.get("qc_db_host") or names or data.get("qc_db_user"):
        qc_id = str(uuid.uuid4())
        new["qc_configs"] = [
            {
                "id": qc_id,
                "label": "預設連線（自動遷移）",
                "env": data.get("qc_db_env", QC_DB_DEFAULTS.get("defaultEnv", "sit")),
                "host": data.get("qc_db_host", ""),
                "port": data.get("qc_db_port"),
                "user": data.get("qc_db_user", ""),
                "names": names,
                "schemas": schemas,
            }
        ]
        new["active_qc_config_id"] = qc_id if names else None
        old_pw = data.get("qc_db_password", "")
        if old_pw:
            new["qc_passwords"] = {qc_id: old_pw}

    new["taxonomy_overrides"] = data.get("taxonomy_overrides") or {}
    return new


def load_settings(user_id: str) -> dict:
    """讀某 user 完整設定（含明文機密）；未存過回空白結構複本。

    舊 flat 格式偵測到即遷移成多 config 結構並「立即持久化」一次——穩定各 config 的 uuid，
    避免每次 load 重產 id 導致 active_id / qc_passwords key 失效。
    """
    data = db.load_user_settings(user_id)
    if not data:
        return _blank_settings()
    if _is_legacy_format(data):
        migrated = _migrate_legacy(data)
        db.save_user_settings(user_id, migrated)  # 持久化一次，使 uuid 穩定
        return migrated
    # 補缺 key + 深複本（避免改到 _NEW_DEFAULT 內的 mutable）
    cur = {**_NEW_DEFAULT, **data}
    cur["provider_tokens"] = dict(cur.get("provider_tokens") or {})
    cur["provider_models"] = dict(cur.get("provider_models") or {})
    cur["qc_passwords"] = dict(cur.get("qc_passwords") or {})
    cur["llm_configs"] = [dict(c) for c in (cur.get("llm_configs") or [])]
    cur["qc_configs"] = [dict(c) for c in (cur.get("qc_configs") or [])]
    cur["taxonomy_overrides"] = dict(cur.get("taxonomy_overrides") or {})
    cur["overview_boards"] = [dict(b) for b in (cur.get("overview_boards") or [])]
    return cur


def effective_llm_dict(s: dict, config_id: str | None = None) -> dict:
    """由指定（或 active）LLM config + 共用 provider_tokens 組出 judge 路徑 flat dict（set_current 入參）。

    config_id 指定某套（初判歸因可選模型推理用）；缺省＝active_llm_config_id。
    指定/active 失效 → 回退 llm_configs[0] → 再無則 _DEFAULT_LLM（stub）。
    保留 client._resolve() 所讀 key（provider/base_url/model/temperature/reasoning_effort/provider_tokens），
    故 judge 路徑（app/judge/llm/client.py）零改動。
    """
    configs = s.get("llm_configs") or []
    want_id = config_id or s.get("active_llm_config_id")
    cfg = next((c for c in configs if c.get("id") == want_id), None)
    if cfg is None and configs:
        cfg = configs[0]
    cfg = cfg or {}
    return {
        "provider": cfg.get("provider", _DEFAULT_LLM["provider"]),
        "base_url": cfg.get("base_url", _DEFAULT_LLM["base_url"]),
        "model": cfg.get("model", _DEFAULT_LLM["model"]),
        "temperature": cfg.get("temperature"),
        "thinking": cfg.get("thinking", "default"),
        "reasoning_effort": cfg.get("reasoning_effort", "default"),
        "provider_tokens": dict(s.get("provider_tokens") or {}),
        "provider_models": dict(s.get("provider_models") or {}),
        "taxonomy_overrides": s.get("taxonomy_overrides") or {},
    }


def _sanitize(cur: dict) -> None:
    """就地修正一致性：dangling active_id 回退首項/None；清除孤立 qc_passwords（config 已不存在）。"""
    llm_ids = {c.get("id") for c in cur.get("llm_configs") or []}
    if cur.get("active_llm_config_id") not in llm_ids:
        cur["active_llm_config_id"] = cur["llm_configs"][0]["id"] if cur.get("llm_configs") else None
    qc_ids = {c.get("id") for c in cur.get("qc_configs") or []}
    if cur.get("active_qc_config_id") not in qc_ids:
        cur["active_qc_config_id"] = cur["qc_configs"][0]["id"] if cur.get("qc_configs") else None
    cur["qc_passwords"] = {
        cid: pw for cid, pw in (cur.get("qc_passwords") or {}).items() if cid in qc_ids
    }
    board_ids = {b.get("id") for b in cur.get("overview_boards") or []}
    if cur.get("active_overview_board_id") not in board_ids:
        cur["active_overview_board_id"] = (
            cur["overview_boards"][0]["id"] if cur.get("overview_boards") else None
        )


def save_settings(user_id: str, patch: dict) -> dict:
    """部分/整包合併寫入某 user。機密（provider_tokens / qc_config.password）空或遮罩值不覆蓋既有。

    qc_configs 內每套可帶 transient `password` 欄位：save 時抽出存入 qc_passwords[config_id]，
    config 本體不落機密。回 masked()。
    """
    cur = load_settings(user_id)

    # ── LLM ──
    if "llm_configs" in patch:
        cur["llm_configs"] = [_ensure_id(c) for c in (patch["llm_configs"] or [])]
    if "active_llm_config_id" in patch:
        cur["active_llm_config_id"] = patch["active_llm_config_id"]
    if "provider_tokens" in patch:
        merged = dict(cur.get("provider_tokens") or {})
        for pid, tok in (patch["provider_tokens"] or {}).items():
            if tok and not _is_masked(tok):
                merged[pid] = tok  # 空/遮罩不覆蓋該 provider 既有真值
        cur["provider_tokens"] = merged
    if "provider_models" in patch:
        cur["provider_models"] = dict(patch.get("provider_models") or {})

    # ── QC DB ──（password 隨各 config 帶入，抽出存 qc_passwords）
    if "qc_configs" in patch:
        new_configs: list[dict] = []
        pw_updates: dict[str, str] = {}
        for c in patch["qc_configs"] or []:
            c = _ensure_id(c)
            pw = c.pop("password", None)  # 機密不存進 config 本體
            if pw and not _is_masked(pw):
                pw_updates[c["id"]] = pw
            new_configs.append(c)
        cur["qc_configs"] = new_configs
        merged_pw = dict(cur.get("qc_passwords") or {})
        merged_pw.update(pw_updates)  # 空/遮罩者已在上面過濾，不覆蓋既有
        cur["qc_passwords"] = merged_pw
    if "active_qc_config_id" in patch:
        cur["active_qc_config_id"] = patch["active_qc_config_id"]

    if "taxonomy_overrides" in patch:
        cur["taxonomy_overrides"] = patch.get("taxonomy_overrides") or {}

    # ── 概覽自訂看板（非機密，整包替換 + 補 id）──
    if "overview_boards" in patch:
        cur["overview_boards"] = [_ensure_id(b) for b in (patch["overview_boards"] or [])]
    if "active_overview_board_id" in patch:
        cur["active_overview_board_id"] = patch["active_overview_board_id"]

    _sanitize(cur)
    db.save_user_settings(user_id, cur)
    return masked(user_id)


def _has_active_token(s: dict) -> bool:
    """active LLM config 的 provider 是否已有 token。"""
    eff = effective_llm_dict(s)
    provider = provider_id_for(eff.get("base_url", ""))
    return bool((s.get("provider_tokens") or {}).get(provider))


def _has_active_qc_password(s: dict) -> bool:
    """active QC config 是否已有 password。"""
    aid = s.get("active_qc_config_id")
    return bool(aid and (s.get("qc_passwords") or {}).get(aid))


def masked(user_id: str) -> dict:
    """回傳給前端：機密 map（provider_tokens / qc_passwords）逐 key 遮罩，附 has_token / has_qc_db_password。"""
    cur = load_settings(user_id)
    cur["has_token"] = _has_active_token(cur)
    cur["has_qc_db_password"] = _has_active_qc_password(cur)
    cur["provider_tokens"] = {p: _mask_secret(t) for p, t in (cur.get("provider_tokens") or {}).items()}
    cur["qc_passwords"] = {c: _mask_secret(p) for c, p in (cur.get("qc_passwords") or {}).items()}
    return cur


def raw(user_id: str) -> dict:
    """完整未遮罩配置（含明文 provider_tokens / qc_passwords）——供設定面板「眼睛顯示全文」與編輯回填。

    ⚠️ 明文回傳機密欄位：僅應在受信任的本地 / 內網環境暴露此端點。
    """
    cur = load_settings(user_id)
    cur["has_token"] = _has_active_token(cur)
    cur["has_qc_db_password"] = _has_active_qc_password(cur)
    return cur


def set_current(settings: dict) -> None:
    """端點注入當前 request 的 effective 設定（effective_llm_dict 產），供 judge 路徑讀取。"""
    _current.set(settings)


def current() -> dict:
    """judge 路徑取當前生效設定；未注入時回 stub 預設（_DEFAULT_LLM + 空 provider_tokens）。"""
    s = _current.get()
    return s if s is not None else effective_llm_dict(_blank_settings())

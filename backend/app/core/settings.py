"""設定持久化（全項目共享，存 DB settings 表固定 __global__ 單例 row）——連線層 + 功能區默認旋鈕層。

結構（settings.data JSON，A schema · 2026-07-22）：
- LLM 連線層：`llm_connections`（{ provider_id: {base_url} }，每供應商一條：openai/gemini/bytedance）
  + `llm_tokens`（{ provider_id: token }，per-provider 機密）+ `provider_models`（各供應商自訂 model 清單）。
- LLM 旋鈕層：`llm_area_defaults`（{ area: {provider: 當前選定, knobs: {provider_id:
  {model,thinking,reasoning_effort,temperature}}} }，**每功能區 × 每供應商各一份**團隊共用默認；
  area ∈ LLM_AREAS，清單見 config/global/llm_model.json 的 `areas[]`）。
  某區某家還沒存過默認時的起點走 `area_provider_seed_knobs()`，不是全區共用 _DEFAULT_LLM。
  ⚠️ 2026-07-30 前為「一區一組旋鈕」的扁平形狀，三個供應商 tab 互相覆蓋（存 openai 就把
  bytedance/gemini 沖掉）。既有資料由 alembic 一次性轉換，**程式碼不留任何舊形狀相容分支**。
- QC DB：`qc_connections`（{ env_id: {host,port,user} }，每環境一條：sit/stage/production）
  + `qc_passwords`（{ env_id: password }，per-env 機密）——與 LLM 連線同構（連線+機密分離兩張 map）。

機密絕不明文回前端：masked() 逐 key 遮罩 llm_tokens / qc_passwords；raw() 供「眼睛顯示全文」與編輯回填。
空/遮罩值 save 不覆蓋既有機密。舊多套 config 結構（A schema 改造前的 llm_configs[]/qc_configs[]）由
load_settings 偵測並自動遷移 + 持久化一次（連線按 provider/env 去重收斂，旋鈕沿用原 active 套當三區初值）。

judge 路徑（llm client）透過 contextvar `current()` 取「端點注入的 effective 設定」——
`effective_llm_dict(s, area=..., overrides=...)` 由指定功能區默認旋鈕 + overrides 覆寫 + 對應供應商連線
組出，保留 client._resolve() 所讀 key（provider/base_url/model/temperature/thinking/reasoning_effort/
api_token/provider_models），故 client.py 零改動。
"""

from __future__ import annotations

import contextvars
import json
import logging
import uuid

from app.core import crypto, db
from app.core.paths import GLOBAL_DIR as _GLOBAL_DIR

_log = logging.getLogger(__name__)

# 跨語言共用的「非機密」全局預設值，按領域拆檔置於 repo 根 config/global/（前端 @config/global/* 同讀）。
# 目錄定位統一由 app.core.paths 提供；後續新增全局配置於此目錄各建一 JSON。
# QC DB 連線預設（port/defaultEnv/environments）；main.py 連線測試的 port fallback 亦取此。
QC_DB_DEFAULTS: dict = json.loads((_GLOBAL_DIR / "qc_db.json").read_text(encoding="utf-8"))
_LLM_DEFAULTS: dict = json.loads((_GLOBAL_DIR / "llm_model.json").read_text(encoding="utf-8"))
# LLM model 下拉的最低版本門檻（僅 gpt-* 受限）；/api/settings/models 動態清單過濾用。
LLM_MODEL_MIN_VERSION: str = _LLM_DEFAULTS.get("modelMinVersion", "5.4")
# LLM 供應商目錄（id/base_url/defaultModels）；model 下拉清單 SSOT，list_models() 讀此（不打 /v1/models）。
LLM_PROVIDERS: list = _LLM_DEFAULTS.get("providers", [])
# 特定 model id 的可配參數能力覆寫（優先於所屬 provider 級預設）；目前三供應商差異僅驗證到 provider
# 級（見 model_capabilities_for），此表暫為空，留給未來已驗證的個別 model 差異使用。
LLM_MODEL_CAPABILITIES: dict = _LLM_DEFAULTS.get("modelCapabilities", {})
# 旋鈕值域（API 層校驗用，SSOT＝llm_model.json 頂層；前端 ALL_THINKING_MODES 同讀一份）。
# "default"＝不覆寫、沿用該功能區默認，屬旋鈕的元值而非供應商參數，故刻意不列入這兩個清單。
# ⚠️ 這兩個值域曾在 API 層被寫死成另一套字面（thinking 舊值域 on/off），與執行層 client.py 實際
# 認得的 Ark 原生三態 enabled/disabled/auto 不一致，害 ByteDance 跑批一律 422——值域一律讀此處，
# 不要在任何一層另抄 Literal。
LLM_THINKING_MODES: tuple[str, ...] = tuple(_LLM_DEFAULTS.get("thinkingModes", []))
LLM_REASONING_EFFORTS: tuple[str, ...] = tuple(_LLM_DEFAULTS.get("reasoning", []))
# 功能區清單（LLM 消費點）：每個前端旋鈕配置槽一個，team 共用默認各一份。
LLM_AREAS: tuple[str, ...] = tuple(
    _LLM_DEFAULTS.get("areas", ["prejudge", "prompt_debug", "sandbox"])
)
# 功能區的出廠預設旋鈕（該區還沒存過團隊默認時的起點）：各區合適的模型檔次差很多——裁決跑批要便宜、
# 改寫 Prompt 要聰明——所以不能全部共用一個 _DEFAULT_LLM。未列的區維持 _DEFAULT_LLM。
LLM_AREA_DEFAULTS: dict = _LLM_DEFAULTS.get("areaDefaults", {})
# QC DB 環境清單（連線 key）：與 qc_db.json 的 environments 對齊（通常 sit/stage/production）。
QC_ENVS: tuple[str, ...] = tuple(
    e["id"] for e in QC_DB_DEFAULTS.get("environments", []) if e.get("id")
)


def model_capabilities_for(model_id: str) -> dict:
    """回某 model 的可配參數能力：thinking 控制形態 / reasoning_effort 值域 / temperature 鎖定規則。

    2026-07-23 依三供應商官方文件全面重寫（各家 doc 連結見 llm_model.json providers[].docs）：
    OpenAI／Gemini 官方文件皆證實**沒有獨立的 thinking 開關參數**，reasoning_effort 本身就是唯一控制面
    （`thinkingControl="effortOnly"`）；ByteDance/Ark 官方 SDK 型別確認 `thinking.type` 是真實原生的
    三態 enum（enabled/disabled/auto，`thinkingControl="nativeSwitch"`，可用狀態見 `thinkingModes`）。
    能力預設取「該 model 所屬 provider」的 provider 級欄位，`modelCapabilities[model_id]` 可對個別 model
    覆寫任一欄位（未登記則沿用 provider 預設）。查無所屬 provider（自訂/未知 model）回 openai 預設。

    Args:
        model_id: LLM model id（如 gpt-5.4-mini）。

    Returns:
        {supportsThinking, thinkingControl, thinkingModes, reasoningEffortOptions,
        temperatureLockedWhenThinking, temperatureAlwaysLocked, lockedTemperatureValue, maxTemperature,
        reasoningOffHint}。reasoningOffHint 僅 nativeSwitch（ByteDance）provider 有值——effortOnly
        provider 沒有「關閉」這個獨立狀態（none 是 reasoning_effort 的正常值之一），故此文案對它們恆為
        空字串；temperatureAlwaysLocked＝不論 thinking 狀態、伺服器端一律忽略自訂 temperature（與
        temperatureLockedWhenThinking「僅思考生效時才鎖」為不同機制，見 llm_model.json modelCapabilities
        的實測案例）。
    """
    owner = _provider_of_model(model_id)
    if owner is None:
        # 靜默回退 openai 能力表會讓前端旋鈕用錯的 thinking 控制形態與 temperature 鎖定規則渲染，
        # 出錯時無跡可循——留一筆 warning。（需要「不猜」語義的呼叫端請改用 provider_id_for_model。）
        _log.warning("model_capabilities_for: 未登記的 model=%r，回退 openai 能力表", model_id)
        owner = next((p for p in LLM_PROVIDERS if p.get("id") == "openai"), {})
    base = {
        "supportsThinking": owner.get("supportsThinking", True),
        "thinkingControl": owner.get("thinkingControl", "effortOnly"),
        "thinkingModes": owner.get("thinkingModes", []),
        "reasoningEffortOptions": owner.get("reasoningEffortOptions")
        or _LLM_DEFAULTS.get("reasoning", []),
        "temperatureLockedWhenThinking": owner.get("temperatureLockedWhenThinking", False),
        "temperatureAlwaysLocked": owner.get("temperatureAlwaysLocked", False),
        "lockedTemperatureValue": owner.get("lockedTemperatureValue", 1),
        "maxTemperature": owner.get("maxTemperature", 2),
        "reasoningOffHint": owner.get("reasoningOffHint", ""),
        "docs": owner.get("docs", {}),
    }
    return {**base, **LLM_MODEL_CAPABILITIES.get(model_id, {})}


def qc_db_env_name(env_id: str | None) -> str:
    """回某 QC DB 環境（sit/stage/production）的 bootstrap database 名（測試連線/列舉 database 的起手庫）。

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

    llm_connections/llm_tokens 以此為 key；judge 路徑 _resolve 亦以此取當前 provider 的 token。
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


def default_base_url_for(provider_id: str) -> str:
    """回某 provider 的官方預設端點（SSOT＝llm_model.json `providers[].base_url`）。

    與 `provider_id_for()` 互為反向操作，故緊鄰擺放成對閱讀。連線未填 base_url 時用它顯式補值：
    空值不可當隱含預設——下游一旦 `or "https://api.openai.com/v1"`，Gemini/ByteDance 會拿自家 token
    打 OpenAI 端點回 401，且錯誤訊息指向完全無關的供應商。

    Args:
        provider_id: openai/gemini/bytedance；未知 id 回退 openai 的端點（OpenAI 相容為大宗）。
    """
    hit = next((p for p in LLM_PROVIDERS if p.get("id") == provider_id), None)
    if hit is None:
        hit = next((p for p in LLM_PROVIDERS if p.get("id") == "openai"), None)
    return str((hit or {}).get("base_url", ""))


def provider_has_responses_api(base_url: str) -> bool:
    """該 base_url 所屬 provider 是否有 `/responses` 端點（SSOT＝llm_model.json `providers[].responsesApi`）。

    這是**端點存在性**的靜態事實，不是 per-model 能力宣告（後者走錯誤驅動探測，見
    `client._wire_api_for`）。之所以必須靜態宣告：Gemini 的 OpenAI 相容層沒有 `/responses`，
    打過去回 **404 而非 400**——400 降級階梯攔不住 404，會變成與結構化輸出完全無關的離奇錯誤。
    此欄同時是單一 kill switch：改回 `absent` 即整條 Responses 路徑關閉，不需動程式碼。

    Args:
        base_url: 連線端點；未知/自訂值由 `provider_id_for` 歸 openai。
    """
    pid = provider_id_for(base_url)
    hit = next((p for p in LLM_PROVIDERS if p.get("id") == pid), {})
    return hit.get("responsesApi") == "supported"


def _mask_secret(tok: str) -> str:
    """機密遮罩：>12 字顯示前 7 + … + 後 4；短值顯 ***；空值顯空字串。"""
    tok = tok or ""
    return (tok[:7] + "…" + tok[-4:]) if len(tok) > 12 else ("***" if tok else "")


def _is_masked(v: object) -> bool:
    """是否為遮罩值（含 *** 或 …）；用於 save 時判斷「不覆蓋既有機密」。"""
    s = str(v or "")
    return "***" in s or "…" in s


# 單套 LLM 旋鈕的非機密預設：area 默認缺項時的底，effective_llm_dict 查無 area 默認時亦回退至此。
_DEFAULT_LLM: dict = {
    "provider": "openai",  # openai | gemini | bytedance | custom
    "base_url": "",  # 空＝OpenAI 預設端點
    "model": (LLM_PROVIDERS or [{}])[0].get(
        "defaultModel", "gpt-5-mini"
    ),  # 讀 llm_model.json 首 provider defaultModel（消除三重維護）
    "temperature": None,  # None＝用 API 預設（gpt-5 系列鎖定不送）
    "thinking": "default",  # default | enabled | disabled | auto（見 LLM_THINKING_MODES）
    "reasoning_effort": "default",  # default + LLM_REASONING_EFFORTS
}

# 全項目共享設定固定 key（settings 表單例 row）：所有 load/save 都用此 key。
# email 身分僅供權限授予查詢（見 permissions），與配置存取解耦。
GLOBAL_SETTINGS_KEY = "__global__"

# thinking 舊值域 → 當前值域（LLM_THINKING_MODES）的純詞彙翻譯表。
# 2026-07-23 LlmKnobs 重寫前，全供應商共用一個假想的 on/off 兩態開關；重寫只改了前端判斷邏輯，
# **沒有正規化已落庫的值**，於是 'on' 一直留在 llm_area_defaults 裡，被前端原樣回送 overrides，
# 再被 API 入口 validator（_one_of vs LLM_THINKING_MODES）擋下 → 整個初判分類請求 422。
# 翻譯不改語義：effortOnly 供應商（openai/gemini）本就不讀 thinking（見 judge/llm/client.py
# _reasoning_kwargs），故當下行為完全不變；日後改切 nativeSwitch 供應商時亦保留使用者原意。
_LEGACY_THINKING_MODES: dict[str, str] = {"on": "enabled", "off": "disabled"}


def _normalize_llm_knobs(area_defaults: dict) -> dict:
    """把各功能區默認旋鈕的舊值域正規化到當前 SSOT 值域（就地修改）。

    讀寫兩端都套用，讓既有環境（他人 dev / prod 的既存 row）在下次讀取時自癒，
    不需人工改 DB；寫入端同步套用則確保新資料一開始就是乾淨值域。

    Args:
        area_defaults: {area: {provider, knobs: {provider_id: 旋鈕}}}。

    Returns:
        同一個 dict（已就地正規化），便於串接呼叫。
    """
    for area_cfg in area_defaults.values():
        if not isinstance(area_cfg, dict):
            continue
        for knobs in (area_cfg.get("knobs") or {}).values():
            if not isinstance(knobs, dict):
                continue
            canonical = _LEGACY_THINKING_MODES.get(knobs.get("thinking") or "")
            if canonical:
                knobs["thinking"] = canonical
    return area_defaults


def _blank_settings() -> dict:
    """全新空白設定（深複本，避免共用 mutable 預設）。"""
    return {
        "llm_connections": {},
        "llm_tokens": {},
        "llm_area_defaults": {},
        "provider_models": {},
        "qc_connections": {},
        "qc_passwords": {},
        "overview_boards": [],
        "active_overview_board_id": None,
        "gdrive_upload_folder_url": None,
    }


# 當前 request 生效的 user 設定（端點 handler 注入）；judge 路徑經 current() 讀取。
# 注入值為 effective_llm_dict() 組出的 flat dict（保留 client._resolve 所讀的 key）。
_current: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "current_settings", default=None
)


def _migrate_configs_to_areas(data: dict) -> dict:
    """舊多套 config 結構（A schema 改造前：llm_configs[]/qc_configs[] + active_id）→ 新連線+功能區默認結構。

    一次性遷移（2026-07-22），偵測依據：資料含 `llm_configs` 或 `qc_configs` 鍵而無 `llm_connections`。
    LLM：每 provider 取其首見 config 的 base_url 組 llm_connections（provider 由 config 自帶或由
    base_url 反推）；token 取該 config 的 per-config llm_tokens（無則退舊 provider_tokens[provider]）。
    active config 的旋鈕（model/thinking/reasoning_effort/temperature）→ 三個 area 的初始默認（皆設為
    同一份，符合「現有配置作為默認」）；查無 active/任何 config 時 area 默認留空，effective_llm_dict
    退 _DEFAULT_LLM。
    QC：比照以 env 為 key 收斂 qc_configs → qc_connections + qc_passwords（同 env 多套時取首見，
    active 優先）。
    密碼/token 值原樣搬移（可能仍是 at-rest 密文，_persist 的 encrypt_secret 對密文冪等，安全）。
    """
    new = _blank_settings()
    new["provider_models"] = dict(data.get("provider_models") or {})
    new["overview_boards"] = [dict(b) for b in (data.get("overview_boards") or [])]
    new["active_overview_board_id"] = data.get("active_overview_board_id")
    new["gdrive_upload_folder_url"] = data.get("gdrive_upload_folder_url")

    # ── LLM：llm_configs[] → llm_connections（per provider）+ llm_area_defaults（三區同初值）──
    llm_configs = data.get("llm_configs") or []
    llm_tokens_by_cfg = data.get("llm_tokens") or {}
    provider_tokens = data.get("provider_tokens") or {}
    active_id = data.get("active_llm_config_id")

    connections: dict[str, dict] = {}
    tokens: dict[str, str] = {}
    for cfg in llm_configs:
        pid = cfg.get("provider") or provider_id_for(cfg.get("base_url") or "")
        if pid not in connections:
            connections[pid] = {"base_url": cfg.get("base_url", "")}
        if pid not in tokens:
            tok = llm_tokens_by_cfg.get(cfg.get("id")) or provider_tokens.get(pid)
            if tok:
                tokens[pid] = tok
    new["llm_connections"] = connections
    new["llm_tokens"] = tokens

    active_cfg = next((c for c in llm_configs if c.get("id") == active_id), None) or (
        llm_configs[0] if llm_configs else None
    )
    if active_cfg:
        pid = active_cfg.get("provider") or provider_id_for(active_cfg.get("base_url") or "")
        knobs = {
            "model": active_cfg.get("model", _DEFAULT_LLM["model"]),
            "thinking": active_cfg.get("thinking", "default"),
            "reasoning_effort": active_cfg.get("reasoning_effort", "default"),
            "temperature": active_cfg.get("temperature"),
        }
        # 舊 config 的 thinking 可能是 on/off 舊值域，遷移時一併翻譯（否則舊值直接被搬進新結構）
        new["llm_area_defaults"] = _normalize_llm_knobs(
            {area: {"provider": pid, "knobs": {pid: dict(knobs)}} for area in LLM_AREAS}
        )

    # ── QC：qc_configs[] → qc_connections（per env）+ qc_passwords（per env）──
    qc_configs = data.get("qc_configs") or []
    qc_passwords_by_cfg = data.get("qc_passwords") or {}
    active_qc_id = data.get("active_qc_config_id")
    qc_sorted = sorted(
        qc_configs, key=lambda c: c.get("id") != active_qc_id
    )  # active 優先（stable）

    qc_connections: dict[str, dict] = {}
    qc_passwords: dict[str, str] = {}
    for cfg in qc_sorted:
        env = cfg.get("env") or QC_DB_DEFAULTS.get("defaultEnv", "sit")
        if env in qc_connections:
            continue
        qc_connections[env] = {
            "host": cfg.get("host", ""),
            "port": cfg.get("port"),
            "user": cfg.get("user", ""),
        }
        pw = qc_passwords_by_cfg.get(cfg.get("id"))
        if pw:
            qc_passwords[env] = pw
    new["qc_connections"] = qc_connections
    new["qc_passwords"] = qc_passwords

    return new


def load_settings() -> dict:
    """讀全項目共享設定（含明文機密）；未存過回空白結構複本。

    舊多套 config 結構（無 `llm_connections` 鍵）偵測到即遷移成新連線+功能區默認結構並「立即持久化」
    一次（穩定 shape，避免每次 load 重跑遷移邏輯）。
    """
    data = db.load_settings_row(GLOBAL_SETTINGS_KEY)
    if not data:
        return _blank_settings()
    if "llm_connections" not in data:
        migrated = _migrate_configs_to_areas(data)
        _persist(migrated)
        return migrated
    # 補缺 key + 深複本（避免改到 _blank_settings 內的 mutable）
    cur = {**_blank_settings(), **data}
    cur["llm_connections"] = {k: dict(v) for k, v in (cur.get("llm_connections") or {}).items()}
    cur["llm_tokens"] = dict(cur.get("llm_tokens") or {})
    # 深一層複本：area 值現在含巢狀 knobs（provider→旋鈕），淺複會讓正規化改到 DB 快取內的原物件
    cur["llm_area_defaults"] = _normalize_llm_knobs(
        {
            area: {
                **dict(cfg),
                "knobs": {p: dict(k) for p, k in (dict(cfg).get("knobs") or {}).items()},
            }
            if isinstance(cfg, dict) and "knobs" in cfg
            else dict(cfg)
            for area, cfg in (cur.get("llm_area_defaults") or {}).items()
        }
    )
    cur["provider_models"] = dict(cur.get("provider_models") or {})
    cur["qc_connections"] = {k: dict(v) for k, v in (cur.get("qc_connections") or {}).items()}
    cur["qc_passwords"] = dict(cur.get("qc_passwords") or {})
    cur["overview_boards"] = [dict(b) for b in (cur.get("overview_boards") or [])]
    _decrypt_secret_maps(cur)  # at-rest 密文 → 明文（下游模組永遠只見明文）
    return cur


def area_seed_knobs(area: str | None) -> dict:
    """某功能區的出廠預設旋鈕：`_DEFAULT_LLM` 疊上 `llm_model.json` 的 `areaDefaults[area]`。

    用於該區還沒存過團隊默認的情況（新功能區、全新環境）。

    Args:
        area: 功能區 key；None／未登記的區直接回 `_DEFAULT_LLM` 副本。
    """
    knobs = dict(_DEFAULT_LLM)
    knobs.update(LLM_AREA_DEFAULTS.get(area or "", {}))
    return knobs


def default_model_for(provider_id: str) -> str:
    """回某 provider 的預設 model（SSOT＝llm_model.json，與前端 `defaultModelFor` 同規則）。

    `providers[].defaultModel` 優先，否則取 `defaultModels[0].id`；未知 provider 回空字串。
    切換供應商時用它決定 model——沿用前一家的 model 名送到另一家一定失敗。
    """
    hit = next((p for p in LLM_PROVIDERS if p.get("id") == provider_id), None)
    if not hit:
        return ""
    return str(hit.get("defaultModel") or (hit.get("defaultModels") or [{}])[0].get("id", ""))


def area_provider_seed_knobs(area: str | None, provider_id: str) -> dict:
    """某功能區 × 某供應商「還沒存過默認」時的起點旋鈕。

    在 `area_seed_knobs(area)` 之上把 **model 換成該供應商自己的預設 model**：功能區 seed 的
    model 是全域預設（openai 系），直接拿去餵 ByteDance／Gemini 必然失敗。thinking／
    reasoning_effort／temperature 維持 `"default"`（＝不覆寫），交由執行層依該 model 的能力表決定。

    Args:
        area: 功能區 key。
        provider_id: 供應商 id。
    """
    knobs = area_seed_knobs(area)
    knobs["provider"] = provider_id
    model = default_model_for(provider_id)
    if model:
        knobs["model"] = model
    return knobs


def effective_llm_dict(s: dict, *, area: str | None = None, overrides: dict | None = None) -> dict:
    """由指定功能區默認旋鈕 + 對應供應商連線組出 judge 路徑 flat dict（set_current 入參）。

    area 指定功能區（prejudge/prompt_debug/sandbox）取旋鈕預設；缺省或該區無默認 → 回退 _DEFAULT_LLM
    （stub）。overrides 為本次執行的臨時旋鈕覆寫（不落庫）：model/thinking/reasoning_effort/provider
    僅非 None 值生效；temperature 有「顯式 null＝本次改用 API 預設」語意，只要 key 存在即覆寫（即使值
    是 None），故獨立判斷。
    連線（base_url/token）一律以「覆寫後」決定的 provider 反查 llm_connections/llm_tokens——換言之
    overrides 也能切換本次用哪個供應商連線,不限於原 area 默認的 provider。回傳的 base_url 保證非空
    （連線未填時補該 provider 官方端點，見 default_base_url_for）。
    provider 走三級解析（見 `_resolve_provider`）：**顯式 overrides.provider > 由 overrides.model
    反推 > 功能區默認**。第二級是必要的——provider 與 model 在 `LlmOverridesIn` 是各自獨立的選填
    欄位，只換 model 而不帶 provider 時若沿用 area 默認，就會拿 A 家 token 打 B 家端點（靜默錯）。
    保留 client._resolve() 所讀 key（provider/base_url/model/temperature/thinking/reasoning_effort/
    api_token/provider_models），故 judge 路徑（app/judge/llm/client.py）零改動。
    """
    area_cfg = (s.get("llm_area_defaults") or {}).get(area or "") or {}
    area_provider = area_cfg.get("provider") or area_seed_knobs(area).get("provider")

    # 先定 provider、再載該家旋鈕：切換供應商就該帶出「那一家自己上次存的」設定，而不是把前一家的
    # thinking／reasoning_effort／temperature 殘留過去（舊實作只重置 model，是跨供應商污染的來源）。
    provider = _resolve_provider(s, {"provider": area_provider}, overrides)
    # 該家沒存過 → 退回「該區 × 該家」的 seed（model 用該供應商自己的預設，不是全域 openai 系預設）
    knobs = {
        **area_provider_seed_knobs(area, provider),
        **dict((area_cfg.get("knobs") or {}).get(provider) or {}),
    }

    if overrides:
        for key in ("model", "thinking", "reasoning_effort"):
            if overrides.get(key) is not None:
                knobs[key] = overrides[key]
        if "temperature" in overrides:
            knobs["temperature"] = overrides["temperature"]
    conn = (s.get("llm_connections") or {}).get(provider) or {}
    return {
        "provider": provider,
        # 收斂點補值：連線沒填（或存過空字串）一律補該 provider 官方端點，讓下游永遠見不到空 base_url
        # （既有已存空值的連線因此自動修好，不需 migration）。
        "base_url": conn.get("base_url") or default_base_url_for(provider),
        "model": knobs.get("model") or _DEFAULT_LLM["model"],
        "temperature": knobs.get("temperature"),
        "thinking": knobs.get("thinking") or "default",
        "reasoning_effort": knobs.get("reasoning_effort") or "default",
        # per-provider token：該供應商連線自身的 token；resolve_provider_token 據此解出
        "api_token": (s.get("llm_tokens") or {}).get(provider, ""),
        "provider_models": dict(s.get("provider_models") or {}),
    }


def _sanitize(cur: dict) -> None:
    """就地修正一致性：清除孤立 llm_tokens/qc_passwords（連線已不存在）；area 默認補全已知功能區。"""
    conn_providers = set(cur.get("llm_connections") or {})
    cur["llm_tokens"] = {
        p: t for p, t in (cur.get("llm_tokens") or {}).items() if p in conn_providers
    }
    qc_envs = set(cur.get("qc_connections") or {})
    cur["qc_passwords"] = {
        e: pw for e, pw in (cur.get("qc_passwords") or {}).items() if e in qc_envs
    }
    board_ids = {b.get("id") for b in cur.get("overview_boards") or []}
    if cur.get("active_overview_board_id") not in board_ids:
        cur["active_overview_board_id"] = (
            cur["overview_boards"][0]["id"] if cur.get("overview_boards") else None
        )


def save_settings(patch: dict) -> dict:
    """部分/整包合併寫入全項目共享設定。機密（llm_tokens / qc_passwords）空或遮罩值不覆蓋既有。

    併發語義：內部 load
    最新→欄位級白名單 merge→整包 persist（競態窗口毫秒級、欄位級合併衝突面小），多人同時
    編輯不同 tab 走 last-write-wins，可接受。
    llm_connections/qc_connections 與 llm_tokens/qc_passwords 為平行 map（keyed by provider/env），
    整包替換非機密連線欄位、機密欄位逐 key merge（空/遮罩不覆蓋既有）。回 masked()。
    """
    cur = load_settings()

    # ── LLM 連線層（provider → base_url；token 另表）──
    if "llm_connections" in patch:
        cur["llm_connections"] = {
            pid: {"base_url": (conn or {}).get("base_url", "")}
            for pid, conn in (patch["llm_connections"] or {}).items()
        }
    if "llm_tokens" in patch:
        merged = dict(cur.get("llm_tokens") or {})
        for pid, tok in (patch["llm_tokens"] or {}).items():
            if tok and not _is_masked(tok):
                merged[pid] = tok  # 空/遮罩不覆蓋該 provider 既有真值
        cur["llm_tokens"] = merged
    if "provider_models" in patch:
        cur["provider_models"] = dict(patch.get("provider_models") or {})

    # ── LLM 旋鈕層（area → {provider, knobs:{provider_id: 旋鈕}}，team 共用默認）──
    # **逐供應商合併，不整包替換**：整包替換正是「存了 openai 就把另外兩家沖掉」的成因。
    # 送來的 knobs 只含哪幾家，就只更新哪幾家；未提及的供應商原封不動。
    if "llm_area_defaults" in patch:
        merged_areas = dict(cur.get("llm_area_defaults") or {})
        for area, incoming in (patch["llm_area_defaults"] or {}).items():
            existing = dict(merged_areas.get(area) or {})
            merged_knobs = {p: dict(k) for p, k in (existing.get("knobs") or {}).items()}
            for pid, knobs in (incoming.get("knobs") or {}).items():
                merged_knobs[pid] = dict(knobs or {})
            merged_areas[area] = {
                # 沒指定當前供應商時保留原選定（例如只是更新某家的旋鈕、不打算切換）
                "provider": incoming.get("provider") or existing.get("provider") or "",
                "knobs": merged_knobs,
            }
        # 寫入端同步正規化：舊前端／腳本送來的舊值域不進庫，庫內值域恆為當前 SSOT
        cur["llm_area_defaults"] = _normalize_llm_knobs(merged_areas)

    # ── QC 連線層（env → host/port/user；password 另表）──
    if "qc_connections" in patch:
        cur["qc_connections"] = {
            env: {k: (conn or {}).get(k) for k in ("host", "port", "user")}
            for env, conn in (patch["qc_connections"] or {}).items()
        }
    if "qc_passwords" in patch:
        merged_pw = dict(cur.get("qc_passwords") or {})
        for env, pw in (patch["qc_passwords"] or {}).items():
            if pw and not _is_masked(pw):
                merged_pw[env] = pw  # 空/遮罩不覆蓋該環境既有真值
        cur["qc_passwords"] = merged_pw

    # ── 概覽自訂看板（非機密，整包替換 + 補 id）──
    if "overview_boards" in patch:
        cur["overview_boards"] = [_ensure_id(b) for b in (patch["overview_boards"] or [])]
    if "active_overview_board_id" in patch:
        cur["active_overview_board_id"] = patch["active_overview_board_id"]

    # ── 導出偏好（非機密）：空字串＝清除（存 None，前端退全域 config 預設）──
    if "gdrive_upload_folder_url" in patch:
        cur["gdrive_upload_folder_url"] = (patch["gdrive_upload_folder_url"] or "").strip() or None

    _sanitize(cur)
    _persist(cur)
    return masked()


def _ensure_id(cfg: dict) -> dict:
    """補 uuid id（前端新建留空時）；回新 dict，不就地改入參。目前僅 overview_boards 沿用此 id 語意。"""
    if cfg.get("id"):
        return dict(cfg)
    return {**cfg, "id": str(uuid.uuid4())}


def _has_any_token(s: dict) -> bool:
    """是否任一供應商連線已配 token（LLM 是否至少可用一套的粗粒度信號）。"""
    return any((s.get("llm_tokens") or {}).values())


def _has_any_qc_password(s: dict) -> bool:
    """是否任一環境 QC 連線已配密碼（粗粒度信號）。"""
    return any((s.get("qc_passwords") or {}).values())


def masked() -> dict:
    """回傳給前端（全項目共享設定）：機密 map 逐 key 遮罩，附粗粒度 has_token / has_qc_db_password
    及逐供應商/逐環境細粒度 provider_has_token / qc_env_has_password（前端各連線卡個別顯示用）。
    """
    cur = load_settings()
    cur["has_token"] = _has_any_token(cur)
    cur["has_qc_db_password"] = _has_any_qc_password(cur)
    cur["provider_has_token"] = {p: bool(t) for p, t in (cur.get("llm_tokens") or {}).items()}
    cur["qc_env_has_password"] = {e: bool(pw) for e, pw in (cur.get("qc_passwords") or {}).items()}
    cur["llm_tokens"] = {p: _mask_secret(t) for p, t in (cur.get("llm_tokens") or {}).items()}
    cur["qc_passwords"] = {e: _mask_secret(pw) for e, pw in (cur.get("qc_passwords") or {}).items()}
    return cur


def raw() -> dict:
    """完整未遮罩配置（全項目共享·含明文 llm_tokens / qc_passwords）——供設定面板「眼睛顯示全文」與編輯回填。

    ⚠️ 明文回傳機密欄位：僅應在受信任的本地 / 內網環境暴露此端點；並由 settings.secret.read 權限 gating。
    """
    cur = load_settings()
    cur["has_token"] = _has_any_token(cur)
    cur["has_qc_db_password"] = _has_any_qc_password(cur)
    return cur


def set_current(settings: dict) -> None:
    """端點注入當前 request 的 effective 設定（effective_llm_dict 產），供 judge 路徑讀取。"""
    _current.set(settings)


def current() -> dict:
    """judge 路徑取當前生效設定；未注入時回 stub 預設（_DEFAULT_LLM + 空 token）。"""
    s = _current.get()
    return s if s is not None else effective_llm_dict(_blank_settings())


def _decrypt_secret_maps(data: dict) -> None:
    """就地把機密 map（llm_tokens / qc_passwords）由 at-rest 密文轉回明文。

    舊明文列直通（crypto.decrypt_secret 對非密文原樣返回），支撐漸進遷移。
    """
    for key in ("llm_tokens", "qc_passwords"):
        data[key] = {k: crypto.decrypt_secret(v) for k, v in (data.get(key) or {}).items()}


def _persist(data: dict) -> None:
    """落庫唯一出口：機密 map 加密後寫 DB（AIQ_SECRET_KEY 未設時明文直通）。

    加密作用在複本，入參 data（呼叫端後續仍持有的明文版）不被污染。encrypt_secret 對已加密值冪等，
    故遷移時原樣搬移的密文（未先解密）在此重新加密不會壞掉（雙重套殼安全，見 crypto.py 文件）。
    """
    stored = dict(data)
    for key in ("llm_tokens", "qc_passwords"):
        stored[key] = {k: crypto.encrypt_secret(v) for k, v in (data.get(key) or {}).items()}
    db.save_settings_row(GLOBAL_SETTINGS_KEY, stored)


def resolve_provider_token(eff: dict) -> str:
    """由 effective LLM dict 解出該配置實際生效的 token（per-provider api_token 優先，OpenAI 才 fallback env）。

    與 judge 路徑 `llm/client._resolve()` 共用同一判定——API 層 stub 硬閘（prejudge router /
    prejudge_batch 第二道防線）據此判斷「本次批量是否將落為 stub 假判」，兩處邏輯合一防漂移
    （曾因 env 空值覆蓋致 stub 假判覆蓋 1,452 筆真歸因）。

    後備分流（provider-aware）：`env.openai_api_key` 只是 **OpenAI** 的 infra 後備；gemini / bytedance
    等非 OpenAI provider 若無連線 token 一律回空（視為未配置），否則會誤拿 OpenAI key 使 stub
    硬閘誤判「已配置」放行，實際卻拿 OpenAI key 打非 OpenAI 端點 → 逐筆 401/403。provider 由 base_url
    反推（未知/自訂端點歸 openai，保留其 env 後備）。

    Args:
        eff: effective LLM dict（`effective_llm_dict()` 產出或 contextvar `current()` 讀出，
            含該供應商連線自身的 api_token 與 base_url；缺鍵視為空）。

    Returns:
        實際生效 token；解不出任何 token 回空字串（呼叫端以 falsy 判 stub）。
    """
    from app.core.config import env  # 函式內 import：維持 settings 不在頂層依賴 config

    # per-provider：直接取該連線自身 token（effective_llm_dict 已解出 api_token）
    per_provider = eff.get("api_token")
    if per_provider:
        return per_provider
    # env 後備僅限 OpenAI（含未知/自訂 OpenAI 相容端點，provider_id_for 預設歸 openai）
    if provider_id_for(eff.get("base_url") or "") == "openai":
        return env.openai_api_key
    return ""


def _provider_of_model(model_id: str) -> dict | None:
    """查某 model id 所屬的 provider 條目；查無回 None（**不猜、不回退**）。

    刻意回 None 而非 fallback：呼叫端對「查不到」的正確反應各不相同——渲染能力表可以退回
    openai 預設（見 `model_capabilities_for`），但解析要打哪個端點**絕不能猜**（見
    `provider_id_for_model`）。把「查不到」的處置權留給呼叫端，這裡只回事實。

    Args:
        model_id: LLM model id（如 gpt-5.4-mini、seed-2-0-lite-260428）。

    Returns:
        `llm_model.json` 的 providers[] 條目；查無回 None。
    """
    return next(
        (
            p
            for p in LLM_PROVIDERS
            if any(m.get("id") == model_id for m in p.get("defaultModels") or [])
        ),
        None,
    )


def provider_id_for_model(model_id: str) -> str:
    """由 model id 反推所屬 provider id（openai/gemini/bytedance）；未登記者**拋錯不猜**。

    為什麼不比照 `provider_id_for(base_url)` 回退 openai：那條的回退有事實基礎（未知 base_url
    多半是 OpenAI 相容端點），而 model id 沒有這種統計性質——拿 ByteDance 的 model 名去猜
    openai，結果是**用 A 家的 token 打 B 家的端點**，且不會報錯、只是結果錯（下游一律回 401
    或更糟的靜默錯誤）。多模型跑批逐一解析每個 model 的端點時，這個「不猜」是正確性前提。

    Args:
        model_id: LLM model id。

    Returns:
        provider id。

    Raises:
        ValueError: 該 model 未登記於 `config/global/llm_model.json` 的 providers[].defaultModels。
    """
    owner = _provider_of_model(model_id)
    if owner is None:
        known = sorted(m.get("id", "") for p in LLM_PROVIDERS for m in p.get("defaultModels") or [])
        raise ValueError(
            f"未登記的 model：{model_id!r}——無法判斷所屬供應商。"
            f"請先加進 config/global/llm_model.json 的 providers[].defaultModels（現有：{known}）"
        )
    return str(owner.get("id", ""))


def _resolve_provider(s: dict, knobs: dict, overrides: dict | None) -> str:
    """決定本次實際要用哪個供應商的連線與 token（三級：顯式 > 由 model 反推 > 功能區默認）。

    **為什麼需要「由 model 反推」這一級**：`LlmOverridesIn` 的 provider 與 model 是各自獨立的
    選填欄位，呼叫端（單次調試、回歸、多模型跑批）常只覆寫 model。舊實作只認顯式 provider，
    於是「只換 model」時 provider 仍停在功能區默認——把 ByteDance 的 model 名配上 OpenAI 的
    token 與端點送出去。**不會報錯，只是結果錯**，是最難察覺的一類缺陷。

    反推同時查兩處 model 來源：`llm_model.json` 的 providers[].defaultModels（內建）與
    settings 的 `provider_models`（使用者自訂清單）——只查前者會讓自訂 model 一律反推失敗。

    反推不到時**保持功能區默認、不拋錯**：自訂 model 名是合法用法，這裡拋錯會打斷既有流程。
    但補一筆 warning，讓「打錯端點」不再無跡可循。需要「絕不猜」語義的呼叫端（多模型跑批逐一
    解析每個 model 的端點）請直接用 `provider_id_for_model()`，那支查不到就拋。

    Args:
        s: 完整 settings dict（需 `provider_models` 以支援自訂 model 反推）。
        knobs: 已套完 overrides 的旋鈕（其 `provider` 可能來自功能區默認而非本次顯式指定）。
        overrides: 本次臨時覆寫；`None`＝全用功能區默認。

    Returns:
        provider id。
    """
    area_default = knobs.get("provider") or _DEFAULT_LLM["provider"]
    ov = overrides or {}

    # ① 顯式指定最優先——保留「overrides 也能切換本次用哪個供應商連線」的既有語義。
    if ov.get("provider"):
        return str(ov["provider"])

    # ② 只換 model 沒指定 provider → 由 model 反推，避免沿用不相干的 area 默認 provider。
    model = ov.get("model")
    if model:
        owner = _provider_of_model(model)
        if owner is not None:
            return str(owner.get("id", "")) or area_default
        for pid, models in (s.get("provider_models") or {}).items():
            if model in (models or []):
                return str(pid)
        _log.warning(
            "effective_llm_dict: model=%r 未登記於 llm_model.json 或 provider_models，"
            "無法反推供應商，沿用功能區默認 provider=%r（若兩者不同家，這次會打到錯的端點）",
            model,
            area_default,
        )

    # ③ 都沒有 → 功能區默認。
    return area_default

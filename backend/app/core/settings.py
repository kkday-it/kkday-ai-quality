"""設定持久化（全項目共享，存 DB settings 表固定 __global__ 單例 row）——連線層 + 具名模型配置庫。

結構（settings.data JSON）：
- LLM 連線層：`llm_connections`（{ provider_id: {base_url} }，每供應商一條：openai/gemini/bytedance）
  + `llm_tokens`（{ provider_id: token }，per-provider 機密）+ `provider_models`（各供應商自訂 model 清單）。
- LLM 模型配置庫：`llm_model_configs`（扁平陣列，每筆 {id, provider, model, thinking,
  reasoning_effort, temperature}）——**全域共用**，一筆可同時被多個功能區引用。**單層**：
  `llm_model.json` 的 `modelConfigs` 只是全新環境的初始內容（`_blank_settings()` 種入），
  之後就是一般配置，沒有「出廠層」，故不存在唯讀／複製／還原出廠這些概念。
  ⚠️ **沒有 `name` 欄位**：名稱由規格衍生（`derive_config_name`），落庫的只有五個旋鈕欄；
  `all_model_configs()` / `masked()` / `raw()` 在讀取端補上這個純投影欄。名稱不落庫就不可能與
  規格漂移，也因此使用者不能自訂名稱（規格相同＝同一筆配置，見 `spec_key`）。
  值域與規格唯一性在**寫入邊界**校驗（見 `_validate_model_configs`），不做 runtime 自癒——
  舊機制正是「爛值先進庫、只能事後洗」的來源。
- LLM 功能區綁定：`llm_area_configs`（{ area: config_id }，哪一區用哪一筆配置）。
  ⚠️ **這是團隊共用的單一份，不是 per-user**（2026-07-31 使用者拍板）：一個人在功能區換了配置，
  同事下次進頁面就會看到新的。這是**刻意的、不是 bug**——曾短暫改存前端 localStorage 求
  「個人選擇互不干擾」，但那樣同事與新裝置永遠拿不到你調好的安排（瀏覽器儲存跨不了人），
  違背「別人能直接用到我提交的配置」的實際需求，故收回 DB。要恢復 per-user 隔離必須先有
  per-user 設定層，不是把它移回瀏覽器。
  綁定**沒有獨立的儲存動作**：功能區下拉選了就即時落庫（見前端 `useLlmAreaConfig`）。
  指向的配置被刪時，於同一次寫入自動剪除該綁定（`save_settings`），讀取端再回落出廠 `areaDefaults`。
  ⚠️ `_blank_settings()` 的初始內容**只對「缺 key」生效**（`load_settings` 是
  `{**_blank_settings(), **data}`）；既有 row 要補內容必須走 migration，改常數沒有用。
- QC DB：`qc_connections`（{ env_id: {host,port,user} }，每環境一條：sit/stage/production）
  + `qc_passwords`（{ env_id: password }，per-env 機密）——與 LLM 連線同構（連線+機密分離兩張 map）。

機密絕不明文回前端：masked() 逐 key 遮罩 llm_tokens / qc_passwords；raw() 供「眼睛顯示全文」與編輯回填。
空/遮罩值 save 不覆蓋既有機密。舊多套 config 結構（llm_configs[]/qc_configs[]）由 load_settings 偵測並
自動遷移 + 持久化一次（連線按 provider/env 去重收斂，原 active 套的旋鈕收成一筆具名配置）。

judge 路徑（llm client）透過 contextvar `current()` 取「端點注入的 effective 設定」——
`effective_llm_dict(s, area=..., overrides=...)` 由 overrides（前端把選中配置解析成的 flat 旋鈕）+
對應供應商連線組出。**後端刻意不認識「配置」這個抽象**：它只吃 flat 旋鈕，旋鈕從哪來（現場調的還是
某個具名配置解析出來的）與它無關——這條邊界換來四條既有端點零改動。
保留 client._resolve() 所讀 key（provider/base_url/model/temperature/thinking/reasoning_effort/
api_token/provider_models），故 client.py 零改動。
"""

from __future__ import annotations

import contextvars
import json
import logging
import math
import uuid

from app.core import crypto, db
from app.core.paths import GLOBAL_DIR as _GLOBAL_DIR

_log = logging.getLogger(__name__)

# 跨語言共用的「非機密」全局預設值，按領域拆檔置於 repo 根 config/global/（前端 @config/global/* 同讀）。
# 目錄定位統一由 app.core.paths 提供；後續新增全局配置於此目錄各建一 JSON。
# QC DB 連線預設（port/defaultEnv/environments）；main.py 連線測試的 port fallback 亦取此。
QC_DB_DEFAULTS: dict = json.loads((_GLOBAL_DIR / "qc_db.json").read_text(encoding="utf-8"))
_LLM_DEFAULTS: dict = json.loads((_GLOBAL_DIR / "llm_model.json").read_text(encoding="utf-8"))
# LLM 供應商目錄（id/base_url/defaultModels/能力欄位）；model 清單與能力判定的 SSOT。
# 前端讀同一份 llm_model.json（features/settings/constants/provider.constant.ts），不另抄一份。
LLM_PROVIDERS: list = _LLM_DEFAULTS.get("providers", [])
# 特定 model id 的可配參數能力覆寫（優先於所屬 provider 級預設，見 model_capabilities_for）。
# ⚠️ 這張表**不是可有可無的微調**：`spec_key()` 的 R1/R2/R3 折疊直接依賴它，填錯會讓配置名與實跑
# 不符、或讓功能相同的兩筆配置並存。2026-07-31 以 144 次真實 API 呼叫逐 model 校正過，改動前請
# 重跑實測（同批推翻了「ByteDance 靜默忽略 temperature」等多條憑文件推斷的錯誤宣告）。
LLM_MODEL_CAPABILITIES: dict = _LLM_DEFAULTS.get("modelCapabilities", {})
# 旋鈕值域（API 層校驗用，SSOT＝llm_model.json 頂層；前端同讀一份）。
# "default"＝不覆寫、沿用該功能區默認，屬旋鈕的元值而非供應商參數，故刻意不列入這兩個清單。
# ⚠️ 這兩個值域曾在 API 層被寫死成另一套字面（thinking 舊值域 on/off），與執行層 client.py 實際
# 認得的 Ark 原生三態 enabled/disabled/auto 不一致，害 ByteDance 跑批一律 422——值域一律讀此處，
# 不要在任何一層另抄 Literal。
LLM_THINKING_MODES: tuple[str, ...] = tuple(_LLM_DEFAULTS.get("thinkingModes", []))
LLM_REASONING_EFFORTS: tuple[str, ...] = tuple(_LLM_DEFAULTS.get("reasoning", []))
# 功能區清單（LLM 消費點）：每個前端旋鈕配置槽一個，team 共用默認各一份。
LLM_AREAS: tuple[str, ...] = tuple(
    _LLM_DEFAULTS.get("areas", ["prejudge", "prompt_debug", "prompt_revise"])
)
# 預設模型配置：**全新環境的初始內容**（由 `_blank_settings()` 種入 DB），之後就是一般配置，
# 與使用者自建的零差別——沒有「出廠層」，故不存在唯讀、複製、還原出廠這些概念。
# ⚠️ 只對「`llm_model_configs` 這個 key 不存在」的 row 生效（`load_settings` 是
# `{**_blank_settings(), **data}`）；既有 row 要補入預設內容必須走 migration，改這裡沒有用。
LLM_DEFAULT_MODEL_CONFIGS: list = _LLM_DEFAULTS.get("modelConfigs", [])
# 功能區的出廠預設配置 id（area → config id）：使用者在該區還沒選過配置時的起點。
# 各區合適的模型檔次差很多——裁決跑批要便宜、改寫 Prompt 要聰明——所以不能全區共用一筆。
LLM_AREA_DEFAULT_CONFIG_IDS: dict = _LLM_DEFAULTS.get("areaDefaults", {})


def model_capabilities_for(model_id: str, provider: str | None = None) -> dict:
    """回某 model 的可配參數能力：thinking 控制形態 / reasoning_effort 值域 / temperature 鎖定規則。

    ⚠️ **判別軸優先取 `provider`**（配置自帶、前後端都持有的事實），與前端 `capabilitiesFor(modelId,
    provider)` 同一套判定；省略時才由 model id 反查 `defaultModels`。這個優先序是必要的——
    自訂／未登記的 model 名反查一定落空，靜默回退 openai 能力表會讓 ByteDance 配置被誤判成
    effortOnly，`spec_key()` 就會把其實**真的會送出去**的 thinking 折掉（見該函式 R1）。

    2026-07-23 依三供應商官方文件全面重寫（各家 doc 連結見 llm_model.json providers[].docs）：
    OpenAI／Gemini 官方文件皆證實**沒有獨立的 thinking 開關參數**，reasoning_effort 本身就是唯一控制面
    （`thinkingControl="effortOnly"`）；ByteDance/Ark 官方 SDK 型別確認 `thinking.type` 是真實原生的
    三態 enum（enabled/disabled/auto，`thinkingControl="nativeSwitch"`，可用狀態見 `thinkingModes`）。
    能力預設取「該 model 所屬 provider」的 provider 級欄位，`modelCapabilities[model_id]` 可對個別 model
    覆寫任一欄位（未登記則沿用 provider 預設）。查無所屬 provider（自訂/未知 model）回 openai 預設。

    Args:
        model_id: LLM model id（如 gpt-5.4-mini）。
        provider: 該 model 所屬供應商 id；帶了就直接用，不再由 model 名反查（自訂 model 亦準確，
            且不會每次都刷一筆「未登記的 model」warning）。

    Returns:
        {supportsThinking, thinkingControl, thinkingModes, reasoningEffortOptions,
        temperatureLockedWhenThinking, temperatureAlwaysLocked, lockedTemperatureValue, maxTemperature,
        reasoningOffHint}。reasoningOffHint 僅 nativeSwitch（ByteDance）provider 有值——effortOnly
        provider 沒有「關閉」這個獨立狀態（none 是 reasoning_effort 的正常值之一），故此文案對它們恆為
        空字串；temperatureAlwaysLocked＝不論 thinking 狀態，API 一律**拒絕**自訂 temperature（只接受預設值），
        與 temperatureLockedWhenThinking「僅推理生效時才拒」為不同機制。
        ⚠️ 這兩欄的值於 2026-07-31 以 144 次真實 API 呼叫逐 model 實測校正——原先「伺服器靜默忽略」
        的說法對 ByteDance seed 系列是錯的（實測送 0.3 正常受理、送 99/-5 才被 InvalidParameter 拒，
        代表它真的會採用），該宣告已改為不鎖。改這兩欄前請重跑實測，勿憑文件字面推斷。
    """
    owner = (
        next((p for p in LLM_PROVIDERS if p.get("id") == provider), None) if provider else None
    ) or _provider_of_model(model_id)
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


#: 一筆模型配置壓縮後的規格鍵：(provider, model, thinking, reasoning_effort, temperature)
ModelConfigKey = tuple[str, str, str, str, float | None]


def spec_key(cfg: dict) -> ModelConfigKey:
    """把一筆模型配置壓成「實際會送出的規格」——唯一性判定與衍生名的共同輸入。

    只折**可證明惰性**的旋鈕，判別軸＝`cfg["provider"]`，與 `client._reasoning_kwargs` 同軸：

    - **R1** effortOnly（openai/gemini）：`client._reasoning_kwargs` 的該分支根本不讀
      `cfg["thinking"]`（只回 `{"reasoning_effort": eff}`）→ 折成 `"default"`。
    - **R2** nativeSwitch（bytedance）且 thinking ∈ {disabled, auto}：該分支不送
      `reasoning_effort`（實測併送回 400 Invalid combination）→ 折成 `"default"`。
    - **R3** temperature：**該組合下 API 不接受自訂溫度時**折成 `None`，否則保留（只 `round(2)`）。
      2026-07-31 逐 model 實測（144 次真實呼叫）確認這同樣是可證明惰性——被鎖的 model 只接受
      預設值（`Only the default (1) value is supported`），而「送預設值」與「不送」行為相同；
      **未被鎖的 model 則會真的採用該值**（ByteDance seed 系列送 0.3 正常受理、送 99/-5 才被拒），
      對它們折疊會改變送出內容，故不折。
      判定 `tempLocked` 用**折疊後**的 thinking/effort（見下方 `_temperature_is_inert`），順序不可顛倒。
      `round(2)`：UI step 是 0.1，兩位小數無損，同時讓規格鍵與名稱的 `:g` 投影一一對應
      （否則 0.1 與 0.1000001 會「鍵不同、名字相同」）。
    - **R4** 空字串／缺鍵一律等同 `"default"`。

    Args:
        cfg: 一筆模型配置（至少需 provider/model；其餘缺鍵按 R4 處理）。

    Returns:
        規格鍵 tuple。**唯一性以此為權威**——tuple 免疫分隔符注入、大小寫與浮點格式化差異。
    """
    provider = str(cfg.get("provider") or "").strip()
    model = str(cfg.get("model") or "").strip()
    thinking = str(cfg.get("thinking") or "default")
    effort = str(cfg.get("reasoning_effort") or "default")

    cap = model_capabilities_for(model, provider)
    native = cap["thinkingControl"] == "nativeSwitch"
    if not native:
        thinking = "default"  # R1
    elif thinking in ("disabled", "auto"):
        effort = "default"  # R2

    temp = cfg.get("temperature")
    temperature = None if temp is None else round(float(temp), 2)
    if temperature is not None and _temperature_is_inert(cap, native, thinking, effort):
        temperature = None  # R3
    return (provider, model, thinking, effort, temperature)


def _temperature_is_inert(cap: dict, native: bool, thinking: str, effort: str) -> bool:
    """該組合下自訂 temperature 是否**送了也沒用**（API 只接受預設值）。

    與前端 `LlmKnobs` 的 `tempLocked` 同一套判定：`temperatureAlwaysLocked` 恆成立；
    `temperatureLockedWhenThinking` 只在「推理生效」時成立——nativeSwitch 供應商看 thinking 是否
    為 enabled/auto，effortOnly 供應商看 reasoning_effort 是否為 none/default 以外的實際檔位。

    Args:
        cap: `model_capabilities_for()` 的結果。
        native: 是否 nativeSwitch 供應商（避免重算）。
        thinking: **已折疊**的 thinking。
        effort: **已折疊**的 reasoning_effort。
    """
    if cap.get("temperatureAlwaysLocked"):
        return True
    reasoning_active = (
        thinking in ("enabled", "auto") if native else effort not in ("none", "default")
    )
    return bool(reasoning_active and cap.get("temperatureLockedWhenThinking"))


def derive_config_name(cfg: dict) -> str:
    """由規格衍生配置名——「名稱就是它的規格」，使用者不能也不需要自己取名。

    版面：``{供應商短標} · {model}[ · thinking:{值}][ · {推理檔位}][ · temp:{值}]``
    旋鈕值一律用 API 原始英文字面（`enabled`/`disabled`/`auto`、`medium`…），不做中文化——
    名稱要能對得上官方文件與錯誤訊息，翻譯反而多一層心智轉換。thinking 加 `thinking:` 前綴是為了
    與同樣是裸 enum 的推理檔位區分；用 f-string 而非對照表，舊值域（`on`/`off`）也能原樣顯示不拋錯。
    經 `spec_key` 折疊後為 default／None 的維度**整段不出現**；折疊後「不出現」與「default」
    一對一，故省略不產生歧義。provider 必須進名稱——`gpt-oss-120b-250805` 掛在 ByteDance 底下
    但名字是 `gpt-` 開頭，且 provider 決定實際打哪個端點、用誰的 token。

    ⚠️ 對外口徑是「名稱＝**宣告**規格」，不是「保證實跑值」：執行層對 400 有 reasoning_effort
    自動降級（見 `client._degrade_reasoning_effort`），名為 `… · xhigh` 的配置仍可能實跑 high。

    Args:
        cfg: 一筆模型配置。

    Returns:
        顯示用名稱字串。純函式，與 `spec_key` 同一份輸入、同一套折疊規則。
    """
    provider, model, thinking, effort, temperature = spec_key(cfg)
    hit = next((p for p in LLM_PROVIDERS if p.get("id") == provider), None)
    parts = [str(hit.get("short_label") or provider) if hit else (provider or "未知供應商"), model]
    if thinking != "default":
        parts.append(f"thinking:{thinking}")
    if effort != "default":
        parts.append(effort)
    if temperature is not None:  # ⚠️ 必用 is not None——0 是合法溫度，falsy 判斷會漏掉
        parts.append(f"temp:{temperature:g}")
    return " · ".join(parts)


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
    `llm.responses_api.WIRE_API_KEY`）。之所以必須靜態宣告：Gemini 的 OpenAI 相容層沒有 `/responses`，
    打過去回 **404 而非 400**——400 降級階梯攔不住 404，會變成與結構化輸出完全無關的離奇錯誤。
    此欄同時是單一 kill switch：改回 `absent` 即整條 Responses 路徑關閉，不需動程式碼。

    Args:
        base_url: 連線端點；未知/自訂值由 `provider_id_for` 歸 openai。
    """
    return provider_has_responses_api_by_id(provider_id_for(base_url))


def provider_has_responses_api_by_id(provider_id: str) -> bool:
    """同上，但直接吃 provider id。

    cfg 自帶 `provider` 時應走這條——從 base_url 反推會把自訂 gateway 上的非 OpenAI
    供應商誤歸 openai（`provider_id_for` 對未知端點的回退），進而誤判端點存在性。
    """
    hit = next((p for p in LLM_PROVIDERS if p.get("id") == provider_id), {})
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
# 沒有正規化已落庫的值，於是 'on' 一直留在庫裡被原樣回送 overrides，再被 API 入口 validator
# 擋下 → 整個初判分類請求 422。
# 翻譯不改語義：effortOnly 供應商（openai/gemini）本就不讀 thinking（見 judge/llm/client.py
# _reasoning_kwargs），故當下行為完全不變；日後改切 nativeSwitch 供應商時亦保留使用者原意。
# 現僅用於 `_migrate_legacy_configs`（極舊 llm_configs[] 結構的一次性搬遷）——新配置的值域由
# `_validate_model_configs` 在寫入邊界擋住，不再需要讀取端自癒。
_LEGACY_THINKING_MODES: dict[str, str] = {"on": "enabled", "off": "disabled"}


def _blank_settings() -> dict:
    """全新空白設定（深複本，避免共用 mutable 預設）。

    `llm_model_configs` 帶入 `LLM_DEFAULT_MODEL_CONFIGS`：全新環境一開機就有得選，不需要另外
    寫 bootstrap 邏輯。⚠️ 只對「缺 key」生效——既有 row 帶著自己的清單，改預設內容不會影響它們。
    """
    return {
        "llm_connections": {},
        "llm_tokens": {},
        "llm_model_configs": [dict(c) for c in _DEFAULT_MODEL_CONFIGS_VALIDATED],
        "llm_area_configs": {},
        "provider_models": {},
        "qc_connections": {},
        "qc_passwords": {},
        "gdrive_upload_folder_url": None,
    }


# 當前 request 生效的 user 設定（端點 handler 注入）；judge 路徑經 current() 讀取。
# 注入值為 effective_llm_dict() 組出的 flat dict（保留 client._resolve 所讀的 key）。
_current: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "current_settings", default=None
)


def _migrate_legacy_configs(data: dict) -> dict:
    """極舊多套 config 結構（llm_configs[]/qc_configs[] + active_id）→ 當前連線 + 具名配置庫結構。

    一次性遷移，偵測依據：資料含 `llm_configs` 或 `qc_configs` 鍵而無 `llm_connections`。
    LLM：每 provider 取其首見 config 的 base_url 組 llm_connections（provider 由 config 自帶或由
    base_url 反推）；token 取該 config 的 per-config llm_tokens（無則退舊 provider_tokens[provider]）。
    active config 的旋鈕（model/thinking/reasoning_effort/temperature）收成 **一筆具名自訂配置**
    （名稱取原 config 的 name，無則以 model 名為底）——舊結構一套 config 就是一組旋鈕，語義上正好
    對應一筆具名配置。查無 active/任何 config 時配置庫留空，生效清單仍有出廠種子可用。
    QC：比照以 env 為 key 收斂 qc_configs → qc_connections + qc_passwords（同 env 多套時取首見，
    active 優先）。
    密碼/token 值原樣搬移（可能仍是 at-rest 密文，_persist 的 encrypt_secret 對密文冪等，安全）。
    """
    new = _blank_settings()
    new["provider_models"] = dict(data.get("provider_models") or {})
    new["gdrive_upload_folder_url"] = data.get("gdrive_upload_folder_url")

    # ── LLM：llm_configs[] → llm_connections（per provider）+ llm_model_configs（active 套收成一筆）──
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
        thinking = active_cfg.get("thinking", "default")
        # **append 到預設內容之後、不是取代它**（`new` 來自 `_blank_settings()`，已含預設配置）：
        # 直接指派會把功能區預設起點整組蓋掉，下一次儲存就會被「預設起點不可刪」擋下。
        # 規格與既有預設重複時直接不建——舊 config 的設定本來就已經在庫裡了。
        new["llm_model_configs"] = repair_model_configs(
            [
                {
                    "provider": pid,
                    "model": active_cfg.get("model", _DEFAULT_LLM["model"]),
                    # 舊 config 的 thinking 可能是 on/off 舊值域，一併翻譯（否則舊值直接搬進新結構）
                    "thinking": _LEGACY_THINKING_MODES.get(thinking, thinking),
                    "reasoning_effort": active_cfg.get("reasoning_effort", "default"),
                    "temperature": active_cfg.get("temperature"),
                }
            ]
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
        migrated = _migrate_legacy_configs(data)
        _persist(migrated)
        return migrated
    # 補缺 key + 深複本（避免改到 _blank_settings 內的 mutable）
    cur = {**_blank_settings(), **data}
    cur["llm_connections"] = {k: dict(v) for k, v in (cur.get("llm_connections") or {}).items()}
    cur["llm_tokens"] = dict(cur.get("llm_tokens") or {})
    cur["llm_model_configs"] = [dict(c) for c in (cur.get("llm_model_configs") or [])]
    cur["provider_models"] = dict(cur.get("provider_models") or {})
    cur["qc_connections"] = {k: dict(v) for k, v in (cur.get("qc_connections") or {}).items()}
    cur["qc_passwords"] = dict(cur.get("qc_passwords") or {})
    _decrypt_secret_maps(cur)  # at-rest 密文 → 明文（下游模組永遠只見明文）
    return cur


# 一筆模型配置裡屬於「旋鈕」的欄位（其餘 id/name 是配置的身分資料，不進 effective dict）。
_KNOB_KEYS: tuple[str, ...] = ("provider", "model", "thinking", "reasoning_effort", "temperature")


def all_model_configs(s: dict) -> list[dict]:
    """生效的模型配置清單（單層＝DB `llm_model_configs`；預設內容由 `_blank_settings()` 首次種入）。

    `name` **不落庫**，在這裡由 `derive_config_name()` 衍生——它是五個旋鈕欄的**純投影**，不是被
    修復的存值，故不牴觸模組 docstring 的「寫入邊界校驗、讀取端不自癒」（實值欄一個都不碰）。
    不落庫的好處：規格一改名字就跟著對，不可能漂移。

    ⚠️ 本函式在每條 judge request 的路徑上（`effective_llm_dict` → `area_default_knobs` →
    `find_model_config`），衍生名是 3 個 provider 的線性查找，量小可忽略——但別再往裡加東西。

    Args:
        s: 完整 settings dict。

    Returns:
        新的 list（元素亦為複本，且已補上衍生的 `name`），呼叫端可安全改寫。
    """
    out: list[dict] = []
    for c in s.get("llm_model_configs") or []:
        cfg = dict(c)
        cfg["name"] = derive_config_name(cfg)
        out.append(cfg)
    return out


def find_model_config(s: dict, config_id: str | None) -> dict | None:
    """依 id 取一筆模型配置（含出廠種子）；`config_id` 為空或查無回 None。"""
    if not config_id:
        return None
    return next((c for c in all_model_configs(s) if c.get("id") == config_id), None)


def area_default_knobs(s: dict, area: str | None) -> dict:
    """某功能區當前生效的旋鈕（`_DEFAULT_LLM` 疊上該區綁定的配置）。

    兩級解析，與前端 `useLlmAreaConfig` 同序：**DB 綁定** `llm_area_configs[area]`
    → 出廠 `areaDefaults[area]`。前端每次執行仍會送完整 overrides，所以這條主要服務
    「呼叫端沒帶 overrides」的路徑——`current()` 的 stub、直打 API 的外部腳本。

    Args:
        s: 完整 settings dict（要在配置庫裡查綁定的配置）。
        area: 功能區 key；None／未登記／指向的配置已不存在 → 回 `_DEFAULT_LLM` 副本。
    """
    knobs = dict(_DEFAULT_LLM)
    key = area or ""
    bound = (s.get("llm_area_configs") or {}).get(key)
    cfg = find_model_config(s, bound) or find_model_config(s, LLM_AREA_DEFAULT_CONFIG_IDS.get(key))
    if cfg:
        knobs.update({k: cfg[k] for k in _KNOB_KEYS if k in cfg})
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


def effective_llm_dict(s: dict, *, area: str | None = None, overrides: dict | None = None) -> dict:
    """由 overrides 旋鈕 + 對應供應商連線組出 judge 路徑 flat dict（set_current 入參）。

    **後端刻意不認識「模型配置」這個抽象**：呼叫端（前端）把使用者選中的具名配置解析成 flat 旋鈕
    後放進 overrides，這裡只吃 flat 值——旋鈕從哪來與本函式無關。這條邊界讓「配置庫」這個新概念
    完全不必滲進 judge 路徑。
    overrides 為本次執行的旋鈕（不落庫）：model/thinking/reasoning_effort/provider 僅非 None 值
    生效；temperature 有「顯式 null＝本次改用 API 預設」語意，只要 key 存在即覆寫（即使值是 None），
    故獨立判斷。
    area 只是 overrides 缺項時的底（`area_default_knobs`，見該函式的適用面說明）；缺省或該區未設
    出廠預設配置 → 回退 _DEFAULT_LLM（stub）。
    連線（base_url/token）一律以「覆寫後」決定的 provider 反查 llm_connections/llm_tokens——換言之
    overrides 也能切換本次用哪個供應商連線，不限於該區出廠預設的 provider。回傳的 base_url 保證非空
    （連線未填時補該 provider 官方端點，見 default_base_url_for）。
    provider 走三級解析（見 `_resolve_provider`）：**顯式 overrides.provider > 由 overrides.model
    反推 > 該區出廠預設**。第二級是必要的——provider 與 model 在 `LlmOverridesIn` 是各自獨立的選填
    欄位，只換 model 而不帶 provider 時若沿用區預設，就會拿 A 家 token 打 B 家端點（靜默錯）。
    保留 client._resolve() 所讀 key（provider/base_url/model/temperature/thinking/reasoning_effort/
    api_token/provider_models），故 judge 路徑（app/judge/llm/client.py）零改動。
    """
    base = area_default_knobs(s, area)
    provider = _resolve_provider(s, base, overrides)
    knobs = dict(base)
    # 換了供應商就要換 model：沿用前一家的 model 名送到另一家必然失敗。overrides 若自帶 model，
    # 下面那圈會再覆蓋回去，所以這裡只是「沒帶 model 卻換了家」時的補救。
    if provider != base.get("provider"):
        knobs["provider"] = provider
        knobs["model"] = default_model_for(provider) or knobs.get("model")

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

    # ── LLM 模型配置庫（全域具名配置，整包替換 + 寫入邊界校驗）──
    # 整包替換而非逐筆 merge：前端持有完整清單、增刪改都是對整份清單操作。
    if "llm_model_configs" in patch:
        cur["llm_model_configs"] = _validate_model_configs(patch.get("llm_model_configs") or [])

    # ── 功能區綁定（area → config id；團隊共用單一份，選了就存，無獨立儲存動作）──
    # ⚠️ 順序關鍵：必須排在 `llm_model_configs` **之後**——同一個 patch 同時換配置庫與改綁定時，
    # 綁定要對著**新**清單校驗，否則指向新配置會被誤判成「配置不存在」。
    if "llm_area_configs" in patch:
        cur["llm_area_configs"] = _validate_area_configs(
            patch.get("llm_area_configs") or {}, cur.get("llm_model_configs") or []
        )
    # 配置被刪 → 剪除指向它的綁定（寫入邊界保持一致，不留指向不存在 id 的死綁定）。
    # 讀取端仍有回落作為第二道防線，但 DB 裡不該存孤兒。
    if "llm_model_configs" in patch:
        live = {str(c.get("id")) for c in (cur.get("llm_model_configs") or []) if c.get("id")}
        cur["llm_area_configs"] = {
            a: cid for a, cid in (cur.get("llm_area_configs") or {}).items() if cid in live
        }

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

    # ── 導出偏好（非機密）：空字串＝清除（存 None，前端退全域 config 預設）──
    if "gdrive_upload_folder_url" in patch:
        cur["gdrive_upload_folder_url"] = (patch["gdrive_upload_folder_url"] or "").strip() or None

    _sanitize(cur)
    _persist(cur)
    return masked()


def _validate_model_configs(configs: list) -> list[dict]:
    """校驗並正規化模型配置；任一筆不合法直接拋 ValueError（路由層轉 400）。

    校驗一律在**寫入邊界**做，讀取端不做自癒：讀取端自癒等於預設「庫裡會有爛值」，而爛值之所以
    進得去正是因為寫入端沒擋——擋住入口才是根治，事後洗只是把問題往後推。
    `settings` 刻意不依賴 fastapi（維持資料層與 API 層解耦），故用內建 ValueError 表達校驗失敗，
    由路由層轉 `HTTPException(400)`（repo 既有多處先例）。

    **落庫的是折疊後的值**（`spec_key` 的 R1/R2 折疊 + temperature round(2)），所以
    「庫內值 ≡ 規格鍵 ≡ 衍生名」三者恆等，不會出現「存的跟名字顯示的不一樣」。

    唯一性走**雙層**：
    - 語義層 `spec_key`（tuple）是權威，免疫分隔符注入、大小寫與浮點格式差異。
    - 顯示層 `name.casefold()` 是下游保護——`prompt_debug._parse_config_entries_form` 對配置名做
      casefold 去重、`PromptDebugBatchDrawer` 拿它當 Vue `:key`。少了這層，`gpt-5.4-mini` 與
      `GPT-5.4-mini`（兩個合法 model id）會是兩筆合法配置卻永遠無法同時跑批；model id 內若含
      ` · ` 則會產生「名字相同、規格不同」的兩列。

    Args:
        configs: 前端送來的**完整**清單（整包替換語義：少送一筆＝刪除該筆）。`name` 欄位會被
            忽略——名稱由規格衍生，不由呼叫端指定。

    Returns:
        正規化後的新 list：欄位收斂到白名單（**不含 name**）、旋鈕折疊、id 缺漏補 uuid。

    Raises:
        ValueError: 供應商未登記、model 空、旋鈕值域外、temperature 非有限非負數、規格或顯示名
            重複、id 撞號、刪掉了功能區預設起點。訊息面向使用者，會原樣顯示在前端。
    """
    known_providers = {str(p.get("id")) for p in LLM_PROVIDERS if p.get("id")}
    valid_thinking = {*LLM_THINKING_MODES, "default"}
    valid_effort = {*LLM_REASONING_EFFORTS, "default"}

    out: list[dict] = []
    seen_keys: dict[ModelConfigKey, str] = {}
    seen_names: dict[str, str] = {}
    for i, raw_cfg in enumerate(configs):
        cfg = dict(raw_cfg or {})
        provider = str(cfg.get("provider") or "").strip()
        model = str(cfg.get("model") or "").strip()
        # 衍生名要 provider/model 合法才算得出來，故這幾條在 name 之前，用 provider/model 定位。
        # 刻意不用序號——序號是整份清單（含其他供應商）的序號，但 UI 是 per-provider 分頁的手風琴，
        # 使用者根本看不到那個序號。
        where = f"（{provider or '未指定供應商'} / {model or '未指定 model'}）"
        if provider not in known_providers:
            raise ValueError(
                f"第 {i + 1} 筆配置{where}的供應商 {provider!r} 未登記"
                f"（可用：{sorted(known_providers)}）"
            )
        if not model:
            raise ValueError(f"第 {i + 1} 筆配置{where}未指定 model")

        thinking = str(cfg.get("thinking") or "default")
        if thinking not in valid_thinking:
            raise ValueError(
                f"第 {i + 1} 筆配置{where}的 thinking={thinking!r} 不在值域 {sorted(valid_thinking)}"
            )
        effort = str(cfg.get("reasoning_effort") or "default")
        if effort not in valid_effort:
            raise ValueError(
                f"第 {i + 1} 筆配置{where}的 reasoning_effort={effort!r} "
                f"不在值域 {sorted(valid_effort)}"
            )

        temperature = cfg.get("temperature")
        if temperature is not None:
            try:
                temperature = float(temperature)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"第 {i + 1} 筆配置{where}的 temperature 必須是數字或留空"
                ) from exc
            # NaN 通過 float() 後會讓「key 已存在」永不命中（NaN != NaN），等於可以無限存入同規格
            # 的列——那正是唯一性宣稱不可能出現的狀態，必須在這裡擋掉。
            if not math.isfinite(temperature) or temperature < 0:
                raise ValueError(f"第 {i + 1} 筆配置{where}的 temperature 必須是 0 以上的有限數字")

        # 折疊後才落庫：庫內值 ≡ 規格鍵 ≡ 衍生名
        probe = {
            "provider": provider,
            "model": model,
            "thinking": thinking,
            "reasoning_effort": effort,
            "temperature": temperature,
        }
        key = spec_key(probe)
        name = derive_config_name(probe)
        if key in seen_keys:
            raise ValueError(
                f"該配置已存在：「{seen_keys[key]}」"
                f"——想調整就直接編輯那一筆；要另建一筆，請至少改動一個參數"
                f"（model / 思考模式 / 推理檔位 / temperature）。"
            )
        if name.casefold() in seen_names:
            raise ValueError(
                f"該配置已存在：「{seen_names[name.casefold()]}」"
                f"——與第 {i + 1} 筆{where}顯示名稱相同，無法在跑批結果中分辨，請改用不同的 model 名。"
            )
        seen_keys[key] = name
        seen_names[name.casefold()] = name

        _, _, folded_thinking, folded_effort, folded_temp = key
        out.append(
            {
                "id": str(cfg.get("id") or "").strip() or str(uuid.uuid4()),
                "provider": provider,
                "model": model,
                "thinking": folded_thinking,
                "reasoning_effort": folded_effort,
                "temperature": folded_temp,
            }
        )

    # id 撞號（前端 bug 或手動 patch）會讓「改 A 存成改 B」，與規格重複同樣要擋
    ids = [c["id"] for c in out]
    if len(set(ids)) != len(ids):
        raise ValueError("模型配置 id 重複")

    # 清單不可為空——這是唯一真正必要的不變式：`useLlmAreaConfig` 的三級回落（選中的 → 該區預設
    # → 清單第一筆）只要清單非空就一定拿得到東西；空了則所有功能區都無配置可用（前端 `ready`
    # 轉 false、執行按鈕全鎖），使用者得先回設定面板新增才能做任何事。
    #
    # ⚠️ 刻意**不**守「`areaDefaults` 指向的 id 不可刪」：那些只是「還沒選過時的起點」，被刪之後
    # 該區回落到清單第一筆——是降級不是損壞，且下拉一直顯示當前用哪一筆（可見、非靜默）。
    # 為此擋住刪除會讓使用者面對「明明用不到卻刪不掉」的摩擦，代價大於收益。後果的告知放在
    # 前端刪除確認框（會列出受影響的功能區與改用哪一筆），不是靠禁止。
    if not out:
        raise ValueError(
            "至少要保留一筆模型配置——清空後所有功能區都沒有配置可用，"
            "必須先回設定面板新增才能繼續操作。"
        )
    return out


def repair_model_configs(configs: list) -> list[dict]:
    """匯入路徑（datapack）專用：把可能過期／重複的舊 blob 修成當前合法形狀，**不拋錯**。

    這是配置庫的**第二條寫入 ingress**——`db/datapack.py` 的 `TABLE_LOAD_ORDER` 首項就是
    `settings`，走 DB 直寫、完全繞過 `_validate_model_configs`。不修的話：匯入一份含重複規格
    （或缺了功能區預設起點）的舊 blob 之後，因為前端 `persist()` 一律送**整份**清單，任何一筆
    配置的任何編輯或刪除都會 400，而且訊息指向使用者根本沒碰過的那筆 → 整個配置庫鎖死。

    修法＝以當前預設內容為底（保證功能區起點存在），再按 `spec_key` 去重後 append 舊列（先到先得）。
    匯入不該因為舊資料不合時宜而失敗，故一律不拋錯。

    Args:
        configs: 匯入 blob 裡的 `llm_model_configs`（形狀可能是任何歷史版本）。

    Returns:
        合法且滿足所有寫入邊界不變式的新清單。
    """
    out = [dict(c) for c in _DEFAULT_MODEL_CONFIGS_VALIDATED]
    seen = {spec_key(c) for c in out}
    ids = {c["id"] for c in out}
    for raw in configs or []:
        cfg = dict(raw or {})
        if (
            str(cfg.get("provider") or "").strip()
            not in {str(p.get("id")) for p in LLM_PROVIDERS if p.get("id")}
            or not str(cfg.get("model") or "").strip()
        ):
            continue  # 壞列直接丟棄——匯入路徑沒有使用者可以回報錯誤
        try:
            key = spec_key(cfg)
        except (TypeError, ValueError):
            continue
        cfg_id = str(cfg.get("id") or "").strip() or str(uuid.uuid4())
        if key in seen or cfg_id in ids:
            continue
        seen.add(key)
        ids.add(cfg_id)
        _, _, thinking, effort, temperature = key
        out.append(
            {
                "id": cfg_id,
                "provider": cfg["provider"],
                "model": cfg["model"],
                "thinking": thinking,
                "reasoning_effort": effort,
                "temperature": temperature,
            }
        )
    return out


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
    # 補上衍生的配置名——`name` 不落庫，前端拿到的必須是投影後的版本，否則整份清單都沒有名字。
    # 這裡與 raw() 是「前端看得到的兩個出口」，兩邊都要投影（漏一邊就是那一邊沒名字）。
    cur["llm_model_configs"] = all_model_configs(cur)
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
    cur["llm_model_configs"] = all_model_configs(cur)  # 同 masked()：補上衍生的配置名
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


# 預設模型配置的「已驗證 + 已折疊」形態，供 `_blank_settings()` 與 `repair_model_configs()` 取用。
# 刻意在 import 期跑一次而非每次呼叫都算：① `llm_model.json` 的 modelConfigs 寫錯（供應商拼錯、
# 旋鈕值域外、規格重複、漏了 areaDefaults 指向的 id）會在**服務啟動當場**炸掉，而不是等到某個
# 使用者按下儲存才發現；② `_blank_settings()` 只需 deep-copy，不必重驗。
# 位置在檔尾是因為它依賴 `_validate_model_configs`；`_blank_settings()` 於呼叫期才解析此名稱，
# 而它不會在 import 期被呼叫，故順序安全。
_DEFAULT_MODEL_CONFIGS_VALIDATED: list[dict] = _validate_model_configs(LLM_DEFAULT_MODEL_CONFIGS)


def _validate_area_configs(mapping: dict, configs: list) -> dict[str, str]:
    """校驗「功能區 → 用哪一筆模型配置」的綁定（寫入邊界，不做讀取端自癒）。

    綁定是**團隊共用的單一份**（見模組 docstring 的拍板理由），所以爛值進庫會讓全團隊一起受害——
    校驗放在寫入邊界，比事後洗庫便宜得多。

    Args:
        mapping: 待寫入的 { area: config_id }。
        configs: **同一次寫入後**的生效配置清單（順序上必須先算好 `llm_model_configs`，
            否則同時換配置庫＋改綁定時會把新配置誤判成不存在）。

    Returns:
        清理後的綁定；值為空字串／None 的項目直接略去（＝清除該區綁定、回落出廠 `areaDefaults`）。

    Raises:
        ValueError: 型別不對、未知功能區、或指向不存在的配置 id（路由層轉 400）。
    """
    if not isinstance(mapping, dict):
        raise ValueError("功能區配置綁定必須是物件（功能區 → 配置 id）")
    live = {str(c.get("id")) for c in (configs or []) if c.get("id")}
    out: dict[str, str] = {}
    for area, config_id in mapping.items():
        area_key = str(area)
        if area_key not in LLM_AREAS:
            raise ValueError(f"未知的功能區「{area_key}」")
        cid = str(config_id or "").strip()
        if not cid:
            continue  # 空值＝清除綁定，不是錯誤
        if cid not in live:
            raise ValueError(f"功能區「{area_key}」指向的模型配置不存在：{cid}")
        out[area_key] = cid
    return out

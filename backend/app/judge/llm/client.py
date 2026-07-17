"""LLM client + stub 開關。

配置來源：當前 user 的 DB user_settings（前端「設定」面板管理）優先，fallback 環境變數（config.env）。
無 api_token（settings 與 env 皆無）→ stub 模式（啟發式，零 key 走通 pipeline）；有 token → 真判。

LLM 呼叫走 OpenAI SDK 直呼（`_complete`）；base_url 可覆寫以打各 OpenAI-compatible 端點。
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections.abc import Callable
from contextvars import ContextVar

from app.core import settings as _settings
from app.core.config import env
from app.judge import run_log

_log = logging.getLogger(__name__)

# ── LLM exact-match 結果快取（diskcache·成熟輪子；env.llm_exact_cache 總開關）──
# key＝model+messages+response_format+temperature+reasoning_effort 的 sha256——prompt 內嵌規則正文，
# 規則/模型/effort 任一變即 miss（失效粒度自動精準）；service_tier 不入 key（僅計價/延遲差異，語義同）。
# 命中＝重用先前初判（同輸入同規則＝同初判，準確度零影響）、零 token、毫秒級回應、不落 llm_usage
# （無 API 呼叫即無花費）。重新初判密集工作流（規則微調→全量重新初判）下未變更部分全免費。
# _cache_read ContextVar：讀取閘（單筆顯式重新初判關讀取＝使用者要求重打；寫入恆開，新結果回填快取）。
_cache_read: ContextVar[bool] = ContextVar("llm_cache_read", default=True)
_exact_cache = (
    None  # lazy 單例（diskcache.Cache：thread/process safe·SQLite 底層·size_limit 自動淘汰）
)
_EXACT_CACHE_LOCK = threading.Lock()


def set_llm_cache_read(enabled: bool) -> None:
    """設定當前 context 的快取「讀取」開關（寫入恆開）。

    單筆顯式重新初判（使用者點「重新初判」＝要求真的重打）關讀取；批次初判開讀取（重用未變更部分）。
    以 ContextVar 承載，prejudge_batch copy_context 派工時 worker 自動繼承。
    """
    _cache_read.set(enabled)


def _get_exact_cache():
    """lazy 建（或取）diskcache 單例；目錄＝data/llm_cache（gitignored，可整刪重生）。"""
    global _exact_cache
    if _exact_cache is None:
        with _EXACT_CACHE_LOCK:
            if _exact_cache is None:
                import diskcache

                from app.core.paths import LLM_CACHE_DIR

                _exact_cache = diskcache.Cache(
                    str(LLM_CACHE_DIR), size_limit=2**30
                )  # 1GB 上限自動淘汰
    return _exact_cache


def _cache_key(kwargs: dict) -> str:
    """完整請求 kwargs → 快取 key（排除 service_tier：serving tier 不改變語義結果）。"""
    payload = {k: v for k, v in kwargs.items() if k != "service_tier"}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def _loads_lenient(raw: str) -> dict | None:
    """容錯 JSON 解析：直 json.loads 失敗時，剝除 markdown fence / 抽取首個 {...} 區塊再試。

    無法送 response_format 的 provider（如 ByteDance seed）靠 prompt 產 JSON，偶帶 ```json fence
    或前後贅述——嚴格 json.loads 會誤判為壞輸出。回 dict；徹底無法解析回 None（上層降級空 dict）。
    """
    if not raw:
        return None
    for candidate in (
        raw,
        raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```"),
    ):
        try:
            v = json.loads(candidate.strip())
            return v if isinstance(v, dict) else None
        except (json.JSONDecodeError, ValueError):
            pass
    start, end = raw.find("{"), raw.rfind("}")  # 抽取首個大括號區塊（贅述包裹時）
    if 0 <= start < end:
        try:
            v = json.loads(raw[start : end + 1])
            return v if isinstance(v, dict) else None
        except (json.JSONDecodeError, ValueError):
            return None
    return None


# token 用量回報槽（ContextVar）：批量初判設定 sink，chat_json 每次呼叫把 usage 回報以累計花費。
# 用 ContextVar 是因 prejudge_batch 以 copy_context 派工，worker 自動繼承 _run 設的 sink。
# sink 簽名：(model, prompt_tokens, completion_tokens, cached_tokens) → None（須自行 thread-safe）；
# cached_tokens＝prompt_tokens 中命中 prompt cache 的部分，供折扣計價。
_usage_sink: ContextVar[Callable[[str, int, int, int], None] | None] = ContextVar(
    "llm_usage_sink", default=None
)


def set_usage_sink(cb: Callable[[str, int, int, int], None] | None) -> None:
    """設定當前 context 的 token 用量回報 sink（批量初判用；None＝不回報）。"""
    _usage_sink.set(cb)


# ── per-call 用量落庫（llm_usage 表）──
# _usage_ctx：呼叫情境 {source, source_id, job_id}，由呼叫端設定（批次於 _run/_work_one、單次於端點）。
# _usage_buffer：批次用暫存 list（於 copy_context 前設定→各 worker 共用同一 list 引用），job 結束 bulk insert；
#   None＝無 buffer→每次呼叫即時單列 insert（ad-hoc 單次呼叫）。
_usage_ctx: ContextVar[dict | None] = ContextVar("llm_usage_ctx", default=None)
_usage_buffer: ContextVar[list | None] = ContextVar("llm_usage_buffer", default=None)


def set_usage_context(ctx: dict | None) -> None:
    """設定當前 context 的用量情境 {source, source_id, job_id}（供 per-call 落庫附註來源）。"""
    _usage_ctx.set(ctx)


def open_usage_buffer() -> list:
    """開啟批次用量暫存並回傳該 list（批次於 copy_context 前呼叫，各 worker 共用；job 結束 flush 落庫）。"""
    buf: list = []
    _usage_buffer.set(buf)
    return buf


def _record_usage(
    stage: str, cfg: dict, prompt: int, completion: int, cached: int, reasoning: int = 0
) -> None:
    """組單次呼叫用量列並落庫（buffer 有則 append 待批量、無則即時 insert）；失敗不阻斷初判。

    cfg["service_tier"] 為**本次實際生效** tier（flex 429 回退標準時呼叫端已改寫）→ 計價對齊帳單。
    """
    from app.core.judge_config import pricing

    model = cfg.get("model", "")
    ctx = _usage_ctx.get() or {}
    row = {
        "stage": stage,
        "model": model,
        "provider": _settings.provider_id_for(cfg.get("base_url", "")),
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "reasoning_tokens": reasoning,
        "cached_tokens": cached,
        "total_tokens": prompt + completion,
        "cost_usd": pricing.cost_usd(
            model, prompt, completion, cached, service_tier=cfg.get("service_tier")
        ),
        "source": ctx.get("source"),
        "source_id": ctx.get("source_id"),
        "job_id": ctx.get("job_id"),
    }
    buf = _usage_buffer.get()
    if buf is not None:
        buf.append(row)
    else:
        from app.core import db

        db.insert_llm_usage_row(row)


# ── flex 回退量測（P1b）：全域計數器，prejudge_batch 於 job 始末取差值 log ──────
# 目的：量測「flex 429 回退標準價」漏掉折扣的占比（>5% 才立項 Batch API lane，見升級計畫 P1b）。
_FLEX_LOCK = threading.Lock()
_flex_counters = {"attempts": 0, "fallbacks": 0}


def flex_stats() -> dict[str, int]:
    """flex serving tier 全域計數快照（{attempts, fallbacks}）；供 job 始末差值量測回退率。

    多 job 併發時差值含同時段他 job 流量（量測目標是全域回退占比，可接受）；計數自 process 啟動累計。
    """
    with _FLEX_LOCK:
        return dict(_flex_counters)


def _flex_bump(key: str) -> None:
    with _FLEX_LOCK:
        _flex_counters[key] += 1


def embed_one(text: str, *, model: str) -> list[float] | None:
    """單文本 embedding（域路由特徵用）；失敗/不可用一律回 None（fail-open，絕不拋）。

    僅 OpenAI provider（base_url 反推）支援；stub / 非 OpenAI / model 空值 → None。usage 以
    stage="router_embed" 落 llm_usage（單價讀 llm_model.json 根層 `embeddings` 表，見 pricing）。

    Args:
        text: 輸入文本（截 8000 字防超 embedding 上限；路由特徵夠用）。
        model: embedding 模型 id（SSOT＝prejudge.json/verdict.json prejudge.domain_router.embedding_model）。

    Returns:
        embedding 向量；不可用回 None（呼叫端 fail-open 全域跑）。
    """
    if not model:
        return None
    cfg = _resolve()
    if not cfg["token"] or _settings.provider_id_for(cfg["base_url"]) != "openai":
        return None
    try:
        cli = _get_client(cfg["token"], cfg["base_url"])
        resp = cli.embeddings.create(model=model, input=text[:8000])
        u = getattr(resp, "usage", None)
        pt = int(getattr(u, "prompt_tokens", 0) or getattr(u, "total_tokens", 0) or 0)
        _record_usage("router_embed", {**cfg, "model": model, "service_tier": None}, pt, 0, 0)
        return list(resp.data[0].embedding)
    except Exception:  # noqa: BLE001  路由輔助，失敗 fail-open（絕不阻斷初判主流程）
        _log.warning("embedding 失敗（model=%s），域路由 fail-open", model, exc_info=True)
        return None


# 共用 OpenAI client 快取（按 token+base_url）：避免每次呼叫新建 connection pool；
# OpenAI SDK client thread-safe，可跨 ThreadPool worker 共用。高併發批量必要的效能優化。
_CLIENT_CACHE: dict[tuple[str, str], object] = {}
_CLIENT_LOCK = threading.Lock()


def _get_client(token: str, base_url: str):
    """取（或建並快取）OpenAI client；附 max_retries（429/5xx 指數退避）+ timeout。

    Args:
        token: API key。
        base_url: 自訂 endpoint（空＝OpenAI 官方）。

    Returns:
        OpenAI client 實例（同 token+base_url 重用，連線池共享）。
    """
    from openai import OpenAI

    key = (token or "", base_url or "")
    with _CLIENT_LOCK:
        cli = _CLIENT_CACHE.get(key)
        if cli is None:
            kwargs: dict = {
                "api_key": token,
                "max_retries": env.llm_max_retries,
                "timeout": float(env.llm_timeout),
            }
            if base_url:
                kwargs["base_url"] = base_url
            cli = OpenAI(**kwargs)
            _CLIENT_CACHE[key] = cli
        return cli


# ── LLM 呼叫（OpenAI SDK 直呼；base_url 可覆寫打各 OpenAI-compatible 端點）─────────
def _complete(cfg: dict, kwargs: dict, cache_key: str | None):
    """OpenAI SDK 呼叫：共用快取 client（含 retry/timeout）+ prompt_cache_key 直傳頂層。

    kwargs＝共用 completion 參數（model/messages/response_format/temperature/reasoning_effort）。
    flex tier 請求以 with_options 拉長 timeout（官方建議 15 分鐘；flex 延遲變動大，沿用標準 60s 必然
    大量誤逾時）——per-request override，不影響快取 client 的標準 timeout。
    """
    client = _get_client(cfg["token"], cfg["base_url"])
    if kwargs.get("service_tier") == "flex":
        client = client.with_options(timeout=float(env.llm_timeout_flex))
    k = dict(kwargs)
    if cache_key:
        k["prompt_cache_key"] = cache_key
    return client.chat.completions.create(**k)


# reasoning_effort 值域隨 provider／model 浮動（實測 2026-07：gpt-5-mini 不吃 none/xhigh、
# gpt-5.4-mini/gpt-5.5 全吃、Gemini 不吃 xhigh、ByteDance seed 不吃 none/xhigh；Google 官方文件
# 亦承認合法值集合會隨版本變動）——故不做靜態白名單，改「錯誤驅動降級」：400 點名 reasoning_effort
# 時就地降級重試（none→minimal、xhigh→high），仍不行則整個拿掉（回 provider 預設）。
_EFFORT_DEGRADE = {"none": "minimal", "xhigh": "high"}


def _degrade_reasoning_effort(kwargs: dict, emsg: str) -> bool:
    """400 錯誤點名 reasoning_effort 時就地降級 kwargs；回傳是否有調整（False＝與此參數無關）。

    三家 provider 的該類 400 訊息皆含字面 "reasoning_effort"（OpenAI "Unsupported value: 'reasoning_effort'…"、
    Gemini "Invalid reasoning_effort: …"、ByteDance "Invalid reasoning_effort: …"），以此為觸發依據。
    """
    if "reasoning_effort" not in emsg or "reasoning_effort" not in kwargs:
        return False
    mapped = _EFFORT_DEGRADE.get(str(kwargs["reasoning_effort"]))
    if mapped:
        kwargs["reasoning_effort"] = mapped
    else:
        kwargs.pop("reasoning_effort", None)  # 已無更低檔可映射 → 拿掉參數走 API 預設
    return True


def _complete_effort_safe(
    cfg: dict, kwargs: dict, cache_key: str | None, stage: str = "", label: str | None = None
):
    """_complete 外掛 reasoning_effort 自動降級：至多降兩級（映射一次 + 移除一次），其餘 400 原樣拋。

    kwargs 就地改寫——呼叫端可事後比對 reasoning_effort 是否被降級（ping 據此回報 note）。
    """
    from openai import BadRequestError

    for _ in range(2):
        try:
            return _complete(cfg, kwargs, cache_key)
        except BadRequestError as e:
            emsg = str(e)
            if not _degrade_reasoning_effort(kwargs, emsg):
                raise
            _log.warning(
                "reasoning_effort 不被接受(stage=%s model=%s)，降級重試：%s",
                stage,
                kwargs.get("model", ""),
                emsg.splitlines()[0][:160],
            )
            run_log.emit(
                "llm_note",
                stage,
                f"reasoning_effort 不被接受，降級為 {kwargs.get('reasoning_effort') or 'API 預設'} 重試",
                {"error": emsg.splitlines()[0][:200]},
                label=label,
            )
    return _complete(cfg, kwargs, cache_key)


def _reasoning_kwargs(cfg: dict) -> dict:
    """依 provider 組出 thinking / reasoning_effort 的實際請求參數（單一出口，chat_json 與 ping 共用）。

    - bytedance（Ark）：原生 `thinking:{type}` 開關走 extra_body（OpenAI SDK 非原生欄位）；實測
      disabled 不可併送 reasoning_effort（400 Invalid combination），故 off 只送開關。
    - openai / gemini：無獨立 thinking 參數，off ≙ reasoning_effort="none"（不支援 none 的 model
      由 _complete_effort_safe 降級為 minimal＝該 model 最低推理檔）。
    - thinking="default"（或缺省）：不干涉，僅依 reasoning_effort 傳遞（既有行為）。
    """
    thinking = cfg.get("thinking")
    eff = cfg.get("reasoning_effort")
    eff = eff if (eff and eff != "default") else None
    provider = _settings.provider_id_for(cfg.get("base_url") or "")
    if provider == "bytedance":
        if thinking == "off":
            return {"extra_body": {"thinking": {"type": "disabled"}}}
        out: dict = {}
        if thinking == "on":
            out["extra_body"] = {"thinking": {"type": "enabled"}}
        if eff:
            out["reasoning_effort"] = eff
        return out
    if thinking == "off":
        return {"reasoning_effort": "none"}
    return {"reasoning_effort": eff} if eff else {}


def _resolve() -> dict:
    """合併當前 request 的 user 設定（contextvar）與 env，回傳實際生效配置。

    token 取「當前 provider（由 base_url 反推）對應的 provider_tokens 條目」，fallback env；
    確保 token 永遠對齊當前 base_url 的 provider，不會用到別家 provider 的 key。
    """
    cfg = _settings.current()
    base_url = (cfg.get("base_url") or "").strip()
    token = _settings.resolve_provider_token(cfg)  # 與 API 層 stub 硬閘共用同一判定，防漂移
    model = cfg.get("model") or env.ai_judge_model
    return {
        "token": token,
        "base_url": base_url,
        "model": model,
        "temperature": cfg.get("temperature"),
        "thinking": cfg.get("thinking", "default"),
        "reasoning_effort": cfg.get("reasoning_effort", "default"),
        # serving tier（"flex"＝-50% 變延遲；批次初判由 prejudge_batch 注入 eff，interactive 呼叫不帶）
        "service_tier": cfg.get("service_tier"),
    }


def has_key() -> bool:
    return bool(_resolve()["token"])


def is_stub() -> bool:
    return not has_key()


def chat_json(
    system: str,
    user: str,
    stage: str = "default",
    *,
    schema: dict | None = None,
    cache_key: str | None = None,
    label: str | None = None,
) -> dict:
    """真 LLM 結構化呼叫。配置取自 user_settings（model/base_url/temperature/reasoning）；
    stage 僅作為解析失敗時的 log 標籤（標示是哪個初判階段），不影響生效配置。

    Args:
        schema: 傳入時用 OpenAI Structured Outputs（response_format=json_schema, strict）——
            生成階段即 token-level 保證輸出符合此 JSON Schema（如 l2_code enum 只吐合法 code）。
            不支援 json_schema 的 provider（回 400）自動回退 json_object（事後由白名單校驗）。
            None＝維持 json_object（極性等不需 enum 的階段）。
        cache_key: OpenAI prompt caching 路由提示（`prompt_cache_key`），把相同前綴的呼叫導向同一
            伺服器提升命中率。**僅 OpenAI 支援**此參數，故依 provider（base_url 反推）判斷才帶，
            避免相容端點（Gemini/ByteDance）拒收。實際命中仍靠「靜態前綴放前、動態放後」的排序。
    """
    cfg = _resolve()
    kwargs: dict = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    if schema is not None:
        kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "attribution", "strict": True, "schema": schema},
        }
    else:
        kwargs["response_format"] = {"type": "json_object"}
    # gpt-5 系列 temperature 鎖定：僅非 None 時送（見 gpt5-temperature-locked）
    if cfg["temperature"] is not None:
        kwargs["temperature"] = float(cfg["temperature"])
    kwargs.update(_reasoning_kwargs(cfg))  # thinking + reasoning_effort per-provider 組參數
    is_openai = _settings.provider_id_for(cfg["base_url"]) == "openai"
    # serving tier：僅 OpenAI 支援 service_tier；"flex"＝-50% 計價換變動延遲（批次初判由
    # prejudge_batch 注入 eff，interactive 呼叫不帶）。非 OpenAI provider 不送（避免 400）。
    tier = cfg.get("service_tier")
    if tier and tier != "default" and is_openai:
        kwargs["service_tier"] = tier
        if tier == "flex":
            _flex_bump("attempts")  # P1b 量測：flex 嘗試數（回退數見 429 handler）
    # prompt_cache_key 僅 OpenAI provider 支援（base_url 反推）；由 _complete 依 gateway 放對位置。
    ck = cache_key if (cache_key and is_openai) else None
    # 執行日誌（僅小批量 job 有 bind，否則 no-op）：LLM 輸入參數 + prompt 全文突出收錄。
    # label＝同一調用分組鍵（polarity / C-1..C-6）→ 前端把 request/prompt/response 聚合成一個 tab。
    log_label = label or stage
    # 100% 對齊：直接記「實際送 API 的完整 kwargs」（去 messages，另存 prompt 全文；token 不在 kwargs，無洩漏）
    req_params = {k: v for k, v in kwargs.items() if k != "messages"}
    req_params["base_url"] = cfg["base_url"] or "https://api.openai.com/v1"
    run_log.emit("llm_request", stage, f"LLM 請求 {cfg['model']}", req_params, label=log_label)
    run_log.emit(
        "llm_prompt", stage, "Prompt 全文", {"system": system, "user": user}, label=log_label
    )
    # exact-match 結果快取：讀取閘開啟才查（單筆顯式重新初判關讀取）；命中＝重用先前初判，
    # 零 API 呼叫、零 token、不落 llm_usage（無花費即無紀錄）。
    use_cache = env.llm_exact_cache
    ekey = _cache_key(kwargs) if use_cache else ""
    if use_cache and _cache_read.get():
        try:
            hit = _get_exact_cache().get(ekey)
        except Exception:  # noqa: BLE001  快取層故障不阻斷初判（退化為直呼）
            hit = None
        if hit is not None:
            _log.debug("LLM exact-cache 命中 stage=%s", stage)
            run_log.emit(
                "llm_response",
                stage,
                "exact-cache 命中（重用先前初判，零 API 呼叫）",
                {"parsed": hit},
                label=log_label,
            )
            return hit
    # typed exceptions 精準分流 SDK 錯誤（取代脆弱的 str(e) 比對）：只有真正的 schema/response_format
    # 400 才做結構降級，timeout/429/5xx（SDK max_retries 已耗盡）一律如實快速失敗——修掉「timeout 被
    # 誤判為 json_schema 不受支援、多做一輪無用 json_object 重試導致逾時翻倍」的 bug。
    from openai import (
        APIConnectionError,
        APITimeoutError,
        BadRequestError,
        InternalServerError,
        RateLimitError,
    )

    t0 = time.monotonic()
    try:
        # OpenAI SDK 呼叫（內建 max_retries 指數退避）；reasoning_effort 值不被該 model 接受時自動降級
        resp = _complete_effort_safe(cfg, kwargs, ck, stage, log_label)
    except RateLimitError as e:
        # flex 容量不足（429 resource_unavailable，該次不計費）＝唯一該重試的 429：回退標準 tier 重打一次
        # （官方建議策略；批次不因 flex 缺容量失敗，僅該筆回原價。cfg 同步改寫使計價按實際 tier）。
        if (
            kwargs.get("service_tier") == "flex"
            and getattr(e, "code", None) == "resource_unavailable"
        ):
            _log.warning("flex 容量不足(stage=%s)，回退標準 tier 重試", stage)
            _flex_bump("fallbacks")  # P1b 量測：該筆以標準價計費（漏掉 -50% 折扣）
            kwargs.pop("service_tier", None)
            cfg = {**cfg, "service_tier": None}
            resp = _complete(cfg, kwargs, ck)
        else:
            raise  # 一般 429：SDK 已依 max_retries 指數退避耗盡→如實拋，不做無用降級重試
    except (APITimeoutError, APIConnectionError):
        raise  # 逾時/連線失敗：與 schema 支援與否無關、SDK 已重試耗盡→如實拋（絕不誤判為 json_schema 問題）
    except InternalServerError:
        raise  # 5xx：SDK 已重試耗盡→如實拋
    except BadRequestError as e:
        # 400 是唯一可能「參數/schema 真不支援」的狀態碼；且降級猜測**只對非 OpenAI 相容端點**做——
        # OpenAI（含 gpt-5）的 400 一律如實拋（多為 prompt 過長/參數非法，降級無濟於事且會掩蓋問題）。
        emsg = str(e)
        if not is_openai and "response_format" in emsg and kwargs.get("response_format"):
            # provider 全不支援 response_format（如 ByteDance seed：json_object / json_schema 皆 400）→
            # 去除該參數重試，靠 system prompt 的 JSON 指示 + 下方 fence-tolerant 解析兜底。
            _log.warning(
                "response_format 不受支援(stage=%s)，改無 response_format 重試：%s",
                stage,
                emsg.splitlines()[0][:160],
            )
            kwargs.pop("response_format", None)
            resp = _complete(cfg, kwargs, ck)
        elif (
            not is_openai
            and schema is not None
            and (
                getattr(e, "param", None) == "response_format"
                or "json_schema" in emsg
                or "schema" in emsg
            )
        ):
            # 相容端點不支援 json_schema Structured Outputs → 回退 json_object（事後由白名單校驗）。
            _log.warning(
                "json_schema 不受支援(stage=%s)，回退 json_object：%s",
                stage,
                emsg.splitlines()[0][:160],
            )
            kwargs["response_format"] = {"type": "json_object"}
            resp = _complete(cfg, kwargs, ck)
        else:
            raise  # OpenAI 的 400、或與 schema/response_format 無關的 400 → 如實拋，不猜測性重試
    # token 用量回報（供批量累計花費；失敗不影響初判）
    sink = _usage_sink.get()
    usage = getattr(resp, "usage", None)
    if usage:
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        # prompt caching 命中：cached_tokens 非零代表靜態前綴（判準法典）已重用、input 計費打折
        details = getattr(usage, "prompt_tokens_details", None)
        cached = int(getattr(details, "cached_tokens", 0) or 0) if details else 0
        # reasoning model（gpt-5 系列）的 completion 內含 reasoning tokens；抽出供量測降 reasoning_effort 空間
        comp_details = getattr(usage, "completion_tokens_details", None)
        reasoning = int(getattr(comp_details, "reasoning_tokens", 0) or 0) if comp_details else 0
        if cached:
            _log.info("prompt cache 命中 stage=%s cached=%d/%d", stage, cached, prompt_tokens)
        if sink:
            try:
                sink(cfg["model"], prompt_tokens, completion_tokens, cached)
            except Exception:  # noqa: BLE001  計費僅輔助，絕不阻斷初判
                _log.debug("usage sink 回報失敗 stage=%s", stage)
        try:  # per-call 落庫（AI 消耗紀錄）；失敗絕不阻斷初判
            _record_usage(stage, cfg, prompt_tokens, completion_tokens, cached, reasoning)
        except Exception:  # noqa: BLE001
            _log.debug("usage 落庫失敗 stage=%s", stage)
    raw = (resp.choices[0].message.content or "{}") if resp.choices else "{}"
    dt_ms = int((time.monotonic() - t0) * 1000)
    comp_d = getattr(usage, "completion_tokens_details", None) if usage else None
    run_log.emit(
        "llm_response",
        stage,
        f"LLM 回應（{dt_ms}ms）",
        {
            "raw": raw,
            "latency_ms": dt_ms,
            "total_tokens": int(getattr(usage, "total_tokens", 0) or 0) if usage else None,
            "reasoning_tokens": (
                int(getattr(comp_d, "reasoning_tokens", 0) or 0) if comp_d else None
            ),
        },
        label=log_label,
    )
    parsed = _loads_lenient(raw)
    if parsed is None:
        # LLM 回非 JSON（空字串 / prompt 漂移）→ 記錄並降級為空 dict，由上層 _sanitize 補位
        # （不靜默吞：留 log 供 monitoring 偵測模型輸出退化）。
        _log.warning("LLM JSON parse 失敗 stage=%s model=%s raw=%r", stage, cfg["model"], raw[:200])
        run_log.emit(
            "error",
            stage,
            "LLM 回非 JSON，降級空 dict 由上層補位",
            {"raw": raw[:500]},
            label=log_label,
        )
        return {}
    if use_cache and parsed:  # 寫入恆開（顯式重新初判的新結果也回填供後續批次重用）；空 dict 不快取
        try:
            _get_exact_cache().set(ekey, parsed, expire=env.llm_cache_ttl_days * 86400)
        except Exception:  # noqa: BLE001  快取寫入失敗不阻斷初判
            _log.debug("LLM exact-cache 寫入失敗 stage=%s", stage)
    return parsed


def ping(prompt: str = "回覆 OK", cfg: dict | None = None) -> dict:
    """測試連線：送一個極短 prompt，回傳終端機顯示用的 I/O。

    cfg=None → 用當前生效（已儲存）設定；傳入 cfg（token/base_url/model/temperature/thinking/
    reasoning_effort）→ 即時測「當前表單值」不經儲存。不丟例外（錯誤收進 error）。
    回 {ok, model, base_url, sent, reply, latency_ms, tokens, error}；無 token → ok=False。
    """
    import time

    cfg = cfg or _resolve()
    base = cfg["base_url"] or "https://api.openai.com/v1"
    if not cfg["token"]:
        return {
            "ok": False,
            "model": cfg["model"],
            "base_url": base,
            "sent": prompt,
            "error": "當前 provider 未設定 token（stub 模式，無法真打 API）；請先填入並儲存該 provider 的 token",
        }

    kwargs: dict = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": "你是連線測試助手，只需極簡短回覆。"},
            {"role": "user", "content": prompt},
        ],
    }
    if cfg["temperature"] is not None:
        kwargs["temperature"] = float(cfg["temperature"])
    kwargs.update(_reasoning_kwargs(cfg))  # thinking + reasoning_effort 組參數（同 chat_json）

    t0 = time.monotonic()
    requested_eff = kwargs.get("reasoning_effort")
    try:
        # 同 chat_json：reasoning_effort 值不被該 model 接受時自動降級
        resp = _complete_effort_safe(cfg, kwargs, None, "ping")
        dt = int((time.monotonic() - t0) * 1000)
        usage = getattr(resp, "usage", None)
        out = {
            "ok": True,
            "model": cfg["model"],
            "base_url": base,
            "sent": prompt,
            "reply": (resp.choices[0].message.content or "").strip(),
            "latency_ms": dt,
            "tokens": getattr(usage, "total_tokens", None) if usage else None,
        }
        final_eff = kwargs.get("reasoning_effort")
        if requested_eff and final_eff != requested_eff:  # 降級過 → 誠實回報實際生效值
            out["note"] = (
                f"reasoning_effort={requested_eff} 不被此 model 接受，"
                f"已自動降級為 {final_eff or 'API 預設'}（初判路徑亦同此行為）"
            )
        return out
    except Exception as e:  # 只回錯誤首行（避免洩漏 key / 堆疊）
        dt = int((time.monotonic() - t0) * 1000)
        return {
            "ok": False,
            "model": cfg["model"],
            "base_url": base,
            "sent": prompt,
            "error": str(e).splitlines()[0][:300],
            "latency_ms": dt,
        }

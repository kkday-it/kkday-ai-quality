"""OpenAI Responses API 單一 gateway（PRD §10.2 / §16）——供應商細節集中封裝。

上層腳本（generator/auditor/judge）不散落供應商參數；全部走 strict Structured Outputs
（Responses API `text.format=json_schema, strict=True`），拒絕額外欄位，分別記錄
refusal／incomplete／parse error。429/5xx 指數退避最多 5 次。永不記錄 API key。

**離線 Prompt Lab 專用**：不 import backend.app、不使用生產 diskcache（PRD §10.4：judge 必須真打、
禁用 exact-match cache）。client 可注入 → fake client 支援零 API 測試（PRD §19）。

Responses API 結構化輸出形狀（openai>=2.x）：
    client.responses.create(model, input=[{role,content}...],
        text={"format": {"type":"json_schema","name":n,"schema":s,"strict":True}})
    → resp.output_text / resp.id / resp.status / resp.usage / resp.output
參考：https://developers.openai.com/api/docs/guides/structured-outputs
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field

_log = logging.getLogger(__name__)

DEFAULT_MAX_RETRIES = 5
_BACKOFF_BASE_S = (
    0.5  # 指數退避基數；實際 = base * 2**attempt（tests 注入 no-op sleep）
)


class RetryableError(Exception):
    """代表 429/5xx/連線逾時等「可重試」錯誤（fake client 亦用它模擬 429/5xx）。"""


@dataclass
class GatewayResult:
    """單次結構化呼叫的統一結果（成功或各類失敗；上層據 error 分類記錄）。"""

    parsed: dict | None  # 解析後 JSON（dict）；失敗為 None
    raw_output: str | None  # 原始輸出文字
    model: str
    status: str  # completed | refusal | incomplete | empty | parse_error | api_error
    error: (
        str | None
    )  # None=成功；否則分類字串（refusal/incomplete/empty/parse_error/api:<msg>）
    request_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int = 0
    attempts: int = 0
    meta: dict = field(
        default_factory=dict
    )  # case/plan id、prompt hash 等溯源（不含 key）

    @property
    def ok(self) -> bool:
        return self.error is None and self.parsed is not None


def _is_retryable(exc: Exception) -> bool:
    """判斷例外是否可重試：本模組 RetryableError，或 openai 的 429/5xx/timeout/connection。"""
    if isinstance(exc, RetryableError):
        return True
    name = type(exc).__name__
    if name in {
        "RateLimitError",
        "APITimeoutError",
        "APIConnectionError",
        "InternalServerError",
    }:
        return True
    status = getattr(exc, "status_code", None)
    return isinstance(status, int) and (status == 429 or 500 <= status < 600)


class Gateway:
    """封裝 OpenAI Responses API 的結構化呼叫 + 退避重試 + 溯源記錄。"""

    def __init__(
        self,
        *,
        client: object | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        temperature: float | None = None,
        reasoning_effort: str | None = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        timeout: float = 120.0,
        sleep=time.sleep,
    ) -> None:
        """建立 gateway。

        Args:
            client: 注入的 responses client（fake 或真）；None → 依 env 延遲建立真 OpenAI client。
            api_key: 覆寫 API key（預設讀 OPENAI_API_KEY）；只用於建 client，絕不記錄。
            base_url: 覆寫端點（OpenAI-compatible）。
            temperature: Responses API 取樣溫度；None 表示不覆寫模型預設。
            reasoning_effort: Responses API `reasoning.effort`；None 表示不覆寫。
            max_retries: 429/5xx 最多重試次數（PRD §10.4：5）。
            timeout: 單次請求逾時秒數。
            sleep: 退避睡眠函式（tests 注入 no-op 以免真等待）。
        """
        self._client = client
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self._base_url = base_url or os.environ.get("OPENAI_BASE_URL", "") or None
        if temperature is not None and not 0 <= temperature <= 2:
            raise ValueError("temperature 必須介於 0 與 2")
        allowed_efforts = {"none", "minimal", "low", "medium", "high", "xhigh", "max"}
        if reasoning_effort is not None and reasoning_effort not in allowed_efforts:
            raise ValueError(f"不支援的 reasoning_effort：{reasoning_effort}")
        self._temperature = temperature
        self._reasoning_effort = reasoning_effort
        self._max_retries = max_retries
        self._timeout = timeout
        self._sleep = sleep

    @property
    def has_key(self) -> bool:
        """是否具備真呼叫能力（有注入 client，或 env 有 key）。"""
        return self._client is not None or bool(self._api_key)

    def _get_client(self):
        """取（或延遲建立）responses client；重庫 lazy import（python.md 規範）。"""
        if self._client is None:
            from openai import OpenAI  # lazy：未真打時不載入 SDK

            kwargs: dict = {
                "api_key": self._api_key,
                "max_retries": 0,
                "timeout": self._timeout,
            }
            # max_retries=0：退避由本 gateway 掌控（統一計數/記錄），不交給 SDK 內部重試
            if self._base_url:
                kwargs["base_url"] = self._base_url
            self._client = OpenAI(**kwargs)
        return self._client

    def structured(
        self,
        *,
        system: str,
        user: str,
        json_schema: dict,
        schema_name: str,
        model: str,
        meta: dict | None = None,
    ) -> GatewayResult:
        """送出 strict Structured Outputs 請求，回統一 GatewayResult。

        成功：parsed 為解析後 dict，error=None。失敗分類記於 error：
        refusal / incomplete / empty / parse_error / api:<首行>。429/5xx 指數退避重試。

        Args:
            system: system 指令（generator/auditor/judge 各自 prompt）。
            user: 已填充占位符的 user 訊息。
            json_schema: strict JSON Schema（judge 取自 prompt；gen/audit 由 Pydantic 匯出）。
            schema_name: schema 名稱（Responses API text.format 需要）。
            model: 模型 id（實際使用者由 CLI/env 決定，一律記錄）。
            meta: 溯源資訊（case_id/plan_id/prompt_sha…），原樣帶回結果（不含 key）。
        """
        meta = dict(meta or {})
        text_format = {
            "format": {
                "type": "json_schema",
                "name": schema_name,
                "schema": json_schema,
                "strict": True,
            }
        }
        payload = {
            "model": model,
            "input": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "text": text_format,
        }
        if self._temperature is not None:
            payload["temperature"] = self._temperature
        if self._reasoning_effort is not None:
            payload["reasoning"] = {"effort": self._reasoning_effort}
        t0 = time.monotonic()
        attempt = 0
        last_exc: Exception | None = None
        while attempt < self._max_retries:
            attempt += 1
            try:
                resp = self._get_client().responses.create(**payload)
                return self._interpret(resp, model, meta, attempt, t0)
            except Exception as e:  # noqa: BLE001  分類：可重試→退避續打；否則記為 api_error
                last_exc = e
                if _is_retryable(e) and attempt < self._max_retries:
                    delay = _BACKOFF_BASE_S * (2 ** (attempt - 1))
                    _log.warning(
                        "gateway 可重試錯誤 attempt=%d/%d 退避 %.1fs：%s",
                        attempt,
                        self._max_retries,
                        delay,
                        type(e).__name__,
                    )
                    self._sleep(delay)
                    continue
                break
        # 重試耗盡或不可重試
        msg = str(last_exc).splitlines()[0][:200] if last_exc else "unknown"
        return GatewayResult(
            parsed=None,
            raw_output=None,
            model=model,
            status="api_error",
            error=f"api:{msg}",
            latency_ms=int((time.monotonic() - t0) * 1000),
            attempts=attempt,
            meta=meta,
        )

    def _interpret(
        self, resp, model: str, meta: dict, attempt: int, t0: float
    ) -> GatewayResult:
        """把 Responses API 回應物件轉為 GatewayResult（refusal/incomplete/parse 分類）。"""
        latency = int((time.monotonic() - t0) * 1000)
        req_id = getattr(resp, "id", None)
        usage = getattr(resp, "usage", None)
        in_tok = int(getattr(usage, "input_tokens", 0) or 0) if usage else None
        out_tok = int(getattr(usage, "output_tokens", 0) or 0) if usage else None
        status = getattr(resp, "status", "completed") or "completed"
        base = {
            "model": model,
            "request_id": req_id,
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "latency_ms": latency,
            "attempts": attempt,
            "meta": meta,
        }
        # refusal 偵測：output 中任一 message content 為 refusal 型
        refusal = _extract_refusal(resp)
        if refusal is not None:
            return GatewayResult(
                parsed=None,
                raw_output=refusal,
                status="refusal",
                error="refusal",
                **base,
            )
        if status == "incomplete":
            return GatewayResult(
                parsed=None,
                raw_output=getattr(resp, "output_text", None),
                status="incomplete",
                error="incomplete",
                **base,
            )
        raw = getattr(resp, "output_text", None)
        if not raw:
            return GatewayResult(
                parsed=None, raw_output=raw, status="empty", error="empty", **base
            )
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return GatewayResult(
                parsed=None,
                raw_output=raw,
                status="parse_error",
                error="parse_error",
                **base,
            )
        if not isinstance(parsed, dict):
            return GatewayResult(
                parsed=None,
                raw_output=raw,
                status="parse_error",
                error="parse_error",
                **base,
            )
        return GatewayResult(
            parsed=parsed, raw_output=raw, status="completed", error=None, **base
        )

    # ── Batch（PRD §10.2 / Phase 5，可選；MVP 不阻塞）──────────────────────────
    def batch_submit(self, requests: list[dict], *, description: str = "") -> dict:
        """提交 Responses Batch（endpoint /v1/responses）；回 {batch_id,file_id}。

        requests：每筆含唯一 custom_id 與 body（同 structured 的 payload）。詳見 batch_runner。
        參考：https://developers.openai.com/api/docs/guides/batch
        """
        client = self._get_client()
        import io

        jsonl = "\n".join(
            json.dumps(
                {
                    "custom_id": r["custom_id"],
                    "method": "POST",
                    "url": "/v1/responses",
                    "body": r["body"],
                },
                ensure_ascii=False,
            )
            for r in requests
        )
        upload = client.files.create(
            file=io.BytesIO(jsonl.encode("utf-8")), purpose="batch"
        )
        batch = client.batches.create(
            input_file_id=upload.id,
            endpoint="/v1/responses",
            completion_window="24h",
            metadata={"description": description} if description else None,
        )
        return {
            "batch_id": batch.id,
            "input_file_id": upload.id,
            "status": batch.status,
        }

    def batch_poll(self, batch_id: str) -> dict:
        """查詢 batch 狀態；回 {status,output_file_id,error_file_id,counts}。"""
        b = self._get_client().batches.retrieve(batch_id)
        return {
            "status": b.status,
            "output_file_id": getattr(b, "output_file_id", None),
            "error_file_id": getattr(b, "error_file_id", None),
            "request_counts": getattr(b, "request_counts", None),
        }

    def batch_download(self, file_id: str) -> str:
        """下載 batch 結果檔（JSONL 文字）；順序不保證，上層以 custom_id 回連（PRD §10.2）。"""
        content = self._get_client().files.content(file_id)
        return (
            content.text if hasattr(content, "text") else content.read().decode("utf-8")
        )


def _extract_refusal(resp) -> str | None:
    """從 Responses 回應的 output 陣列抽出 refusal 文字（無則 None）。"""
    output = getattr(resp, "output", None)
    if not output:
        return None
    for item in output:
        content = getattr(item, "content", None) or []
        for c in content:
            if getattr(c, "type", None) == "refusal":
                return getattr(c, "refusal", "") or "refusal"
    return None

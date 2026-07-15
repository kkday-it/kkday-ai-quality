"""Gemini OpenAI-compatible gateway——仅供 Prompt Lab 的独立模型角色使用。

Gemini 的 OpenAI compatibility endpoint 使用 Chat Completions，而现有 ``Gateway``
使用 OpenAI Responses API；两者请求/回传形状不同，因此在这里集中适配，向上层仍暴露
相同的 ``structured(...) -> GatewayResult`` 契约。

官方端点与模型：
https://ai.google.dev/gemini-api/docs/openai
https://ai.google.dev/gemini-api/docs/whats-new-gemini-3.5
"""

from __future__ import annotations

import json
import logging
import os
import time

from openai_gateway import DEFAULT_MAX_RETRIES, GatewayResult, _is_retryable

_log = logging.getLogger(__name__)

DEFAULT_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
_BACKOFF_BASE_S = 0.5


class GeminiGateway:
    """以 Gemini API 执行 JSON Schema 结构化调用。"""

    def __init__(
        self,
        *,
        client: object | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        timeout: float = 120.0,
        sleep=time.sleep,
        reasoning_effort: str | None = None,
    ) -> None:
        self._client = client
        self._api_key = api_key or os.environ.get("GEMINI_API_KEY", "")
        self._base_url = (
            base_url
            or os.environ.get("GEMINI_BASE_URL", "")
            or DEFAULT_GEMINI_BASE_URL
        )
        self._max_retries = max_retries
        self._timeout = timeout
        self._sleep = sleep
        self._reasoning_effort = reasoning_effort or os.environ.get(
            "PROMPT_LAB_GEMINI_REASONING_EFFORT", ""
        )

    @property
    def has_key(self) -> bool:
        return self._client is not None or bool(self._api_key)

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(
                api_key=self._api_key,
                base_url=self._base_url,
                max_retries=0,
                timeout=self._timeout,
            )
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
        """调用 Gemini Chat Completions，并转换成 Prompt Lab 统一结果。"""
        meta = dict(meta or {})
        payload: dict = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "schema": json_schema,
                    "strict": True,
                },
            },
        }
        # Gemini 3.5 默认 medium；不显式配置时沿用官方默认。
        if self._reasoning_effort:
            payload["reasoning_effort"] = self._reasoning_effort

        t0 = time.monotonic()
        attempt = 0
        last_exc: Exception | None = None
        while attempt < self._max_retries:
            attempt += 1
            try:
                resp = self._get_client().chat.completions.create(**payload)
                return self._interpret(resp, model, meta, attempt, t0)
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if _is_retryable(exc) and attempt < self._max_retries:
                    delay = _BACKOFF_BASE_S * (2 ** (attempt - 1))
                    _log.warning(
                        "Gemini gateway 可重试错误 attempt=%d/%d 退避 %.1fs：%s",
                        attempt,
                        self._max_retries,
                        delay,
                        type(exc).__name__,
                    )
                    self._sleep(delay)
                    continue
                break
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

    @staticmethod
    def _interpret(resp, model: str, meta: dict, attempt: int, t0: float) -> GatewayResult:
        latency = int((time.monotonic() - t0) * 1000)
        request_id = getattr(resp, "id", None)
        usage = getattr(resp, "usage", None)
        input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0) if usage else None
        output_tokens = (
            int(getattr(usage, "completion_tokens", 0) or 0) if usage else None
        )
        base = {
            "model": model,
            "request_id": request_id,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "latency_ms": latency,
            "attempts": attempt,
            "meta": meta,
        }
        choices = getattr(resp, "choices", None) or []
        if not choices:
            return GatewayResult(
                parsed=None, raw_output=None, status="empty", error="empty", **base
            )
        choice = choices[0]
        message = getattr(choice, "message", None)
        refusal = getattr(message, "refusal", None) if message else None
        if refusal:
            return GatewayResult(
                parsed=None,
                raw_output=str(refusal),
                status="refusal",
                error="refusal",
                **base,
            )
        finish_reason = getattr(choice, "finish_reason", None)
        raw = getattr(message, "content", None) if message else None
        if finish_reason not in (None, "stop"):
            return GatewayResult(
                parsed=None,
                raw_output=raw,
                status="incomplete",
                error="incomplete",
                **base,
            )
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
            parsed=parsed,
            raw_output=raw,
            status="completed",
            error=None,
            **base,
        )


def provider_for_model(model: str, configured: str = "auto") -> str:
    """解析 Generator provider；auto 时以官方 Gemini model id 前缀路由。"""
    provider = configured.strip().lower() or "auto"
    if provider == "auto":
        return "gemini" if model.strip().lower().startswith("gemini-") else "openai"
    if provider not in {"openai", "gemini"}:
        raise ValueError(f"不支持的 Generator provider：{configured!r}")
    return provider

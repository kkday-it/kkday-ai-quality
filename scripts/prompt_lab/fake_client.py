"""Fake Responses client（PRD §19 / §21：fake-client 集成測試、dry-run 零 API）。

模擬 openai Responses API 的**回應物件形狀**，讓 Gateway 走與生產完全相同的程式路徑，
但不觸網。由 responder callable 驅動，可回傳：
    - dict            → completed，output_text = JSON
    - str             → completed，output_text = 該字串（測 parse_error 用非 JSON 字串）
    - Exception 實例   → 直接 raise（測 429/5xx 退避：raise RetryableError）
    - ("refusal", msg) → refusal 型輸出
    - ("incomplete", partial) → status=incomplete

不屬正式交付 CLI，僅測試/smoke 用；不呼叫任何網路。
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Callable


def _mk_response(
    *,
    output_text: str | None,
    status: str = "completed",
    refusal: str | None = None,
    rid: str = "resp_fake",
    in_tok: int = 10,
    out_tok: int = 20,
):
    """組出一個結構等同 Responses API 回應的物件（供 Gateway._interpret 讀取）。"""
    output: list = []
    if refusal is not None:
        output = [
            SimpleNamespace(content=[SimpleNamespace(type="refusal", refusal=refusal)])
        ]
    elif output_text is not None:
        output = [
            SimpleNamespace(
                content=[SimpleNamespace(type="output_text", text=output_text)]
            )
        ]
    return SimpleNamespace(
        id=rid,
        status=status,
        output_text=output_text,
        output=output,
        usage=SimpleNamespace(input_tokens=in_tok, output_tokens=out_tok),
    )


class _Responses:
    """`.responses` 子物件，提供 create()。"""

    def __init__(self, responder: Callable):
        self._responder = responder
        self._n = 0

    def create(self, *, model: str, input: list, text: dict, **_kw):  # noqa: A002  對齊 SDK 參數名
        """模擬 responses.create；依 responder 回傳決定輸出/例外/refusal/incomplete。"""
        self._n += 1
        system = next((m["content"] for m in input if m["role"] == "system"), "")
        user = next((m["content"] for m in input if m["role"] == "user"), "")
        fmt = text.get("format", {})
        reply = self._responder(
            system, user, fmt.get("name", ""), fmt.get("schema", {}), self._n
        )
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, tuple) and reply and reply[0] == "refusal":
            return _mk_response(
                output_text=None,
                status="completed",
                refusal=reply[1] if len(reply) > 1 else "拒答",
            )
        if isinstance(reply, tuple) and reply and reply[0] == "incomplete":
            return _mk_response(
                output_text=reply[1] if len(reply) > 1 else None, status="incomplete"
            )
        if isinstance(reply, str):
            return _mk_response(output_text=reply)
        if isinstance(reply, dict):
            return _mk_response(output_text=json.dumps(reply, ensure_ascii=False))
        raise TypeError(f"responder 回傳型別不支援：{type(reply)}")


class FakeResponsesClient:
    """注入 Gateway(client=...) 的假 client；含 .responses.create、.files、.batches（batch 測試用）。"""

    def __init__(self, responder: Callable):
        """responder(system, user, schema_name, json_schema, call_index) -> reply。"""
        self.responses = _Responses(responder)

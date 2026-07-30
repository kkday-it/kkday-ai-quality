"""Chat Completions ↔ Responses API 的 wire-format 翻譯層（非串流 + 串流）。

**為何需要**：strict 結構化輸出（由 API 端強制模型只能吐符合 schema 的 token）在不同供應商
落在不同端點上——2026-07-30 實測：

| 供應商 / 模型 | Chat Completions + strict | Responses API + strict |
|---|---|---|
| OpenAI `gpt-5-mini` | ✅ | ✅ |
| Gemini（OpenAI 相容層） | ✅ | ❌ 404，**根本沒有 /responses 端點** |
| Ark `seed-2-0-lite-260228` | ✅ | ✅ |
| Ark `seed-2-0-lite-260428`（新版） | ❌ 400 | ✅ |

沒有單一端點能通吃，故走「按能力路由」：專案內 kwargs 的**正規形狀恆為 Chat Completions**，
本模組只在真正打 API 的最後一吋做形狀轉換，並把 Responses 回應還原成既有消費端讀得懂的
`.choices[0].message.content` / `.id` / `.usage.*`。這樣加入 Responses 這一階**不需要改任何呼叫端**，
也不動 `_cache_key` / `_settle_request_shape` / `_degrade_reasoning_effort` 三個既有契約。
"""

from __future__ import annotations

import logging
from typing import Any

_log = logging.getLogger(__name__)

# 路由標記：Chat 正規 kwargs 內的私有鍵，宣告本次請求要走哪個 wire API。
# 刻意放進 kwargs（而非 cfg）——降級階梯都是就地改寫 kwargs，且 prompt_debug_batch 的
# `_settle_request_shape` 正是靠複製 kwargs 把「收斂後形狀」帶給所有 worker；放 kwargs 才能
# 沿用這兩套既有機制，不必另開跨 worker 傳遞通道。
WIRE_API_KEY = "_wire_api"
WIRE_RESPONSES = "responses"


def to_responses_kwargs(chat_kwargs: dict[str, Any]) -> dict[str, Any]:
    """Chat Completions kwargs → Responses API kwargs（不改動入參）。

    對照表（皆為對真實端點的實測結論）：
        messages                    → input（role/content 列表可直接沿用）
        response_format.json_schema → text.format（name/strict/schema 提升一層）
        response_format.json_object → text.format.type = "json_object"
        reasoning_effort（扁平）     → reasoning.effort  ← Ark 對扁平寫法回 400 unknown field
        max_tokens                  → max_output_tokens
        extra_body.thinking         → 原樣（Ark 原生開關，enabled/disabled 兩態實測皆可）

    未列舉的鍵原樣透傳：降級階梯會動態增刪 `service_tier` / `prompt_cache_key` 等鍵，這裡硬拋
    會把「某個參數名不合」升級成「整批失敗」，不划算。

    Raises:
        ValueError: 帶 stream 卻走非串流入口（正常路徑不會發生，此為防呆——串流請用
            `to_chat_stream` 包裝 `client.responses.create(stream=True)` 的結果）。
    """
    if chat_kwargs.get("stream"):
        raise ValueError("串流請走 to_chat_stream，勿用 to_responses_kwargs 直接送出")

    out: dict[str, Any] = {}
    for key, val in chat_kwargs.items():
        if key == WIRE_API_KEY:
            continue  # 私有路由標記不外送
        if key == "messages":
            out["input"] = val
        elif key == "response_format":
            out["text"] = {"format": _to_text_format(val)}
        elif key == "reasoning_effort":
            out["reasoning"] = {"effort": val}
        elif key == "max_tokens":
            out["max_output_tokens"] = val
        else:
            out[key] = val
    return out


def _to_text_format(response_format: dict[str, Any] | None) -> dict[str, Any]:
    """response_format → Responses 的 text.format（json_schema 需把 name/strict/schema 提升一層）。"""
    rf = response_format or {}
    if rf.get("type") == "json_schema":
        js = rf.get("json_schema") or {}
        return {
            "type": "json_schema",
            "name": js.get("name", "output"),
            "strict": bool(js.get("strict", True)),
            "schema": js.get("schema", {}),
        }
    return {"type": rf.get("type", "json_object")}


# ── 回應形狀還原 ─────────────────────────────────────────────────────────────────────────
# 只還原消費端**實際讀取**的三組欄位：`.id`（跑批落 jsonl 的 request_id）、
# `.choices[0].message.content`（正文）、`.usage.*`（計價與用量）。全 repo grep 確認
# `model` / `finish_reason` / `refusal` / `tool_calls` 無人讀取，不還原。


class _Cached:
    def __init__(self, cached_tokens: int) -> None:
        self.cached_tokens = cached_tokens


class _Reasoning:
    def __init__(self, reasoning_tokens: int) -> None:
        self.reasoning_tokens = reasoning_tokens


class _Usage:
    """Responses usage → Chat usage 形狀（input_tokens→prompt_tokens 等四個計數）。

    欄位名整組不同，抽錯不會報錯、只會讓 AI 消耗看板的數字慢慢歪掉，故此處逐欄顯式映射。
    """

    def __init__(self, u: Any) -> None:
        self.prompt_tokens = int(getattr(u, "input_tokens", 0) or 0)
        self.completion_tokens = int(getattr(u, "output_tokens", 0) or 0)
        self.total_tokens = int(
            getattr(u, "total_tokens", 0) or (self.prompt_tokens + self.completion_tokens)
        )
        in_d = getattr(u, "input_tokens_details", None)
        out_d = getattr(u, "output_tokens_details", None)
        self.prompt_tokens_details = _Cached(int(getattr(in_d, "cached_tokens", 0) or 0))
        self.completion_tokens_details = _Reasoning(int(getattr(out_d, "reasoning_tokens", 0) or 0))


class _Message:
    def __init__(self, content: str) -> None:
        self.content = content


class _Choice:
    def __init__(self, content: str) -> None:
        self.message = _Message(content)
        self.delta = _Message(content)  # 串流路徑讀 .delta.content，非串流讀 .message.content


class ChatShapeResponse:
    """Responses 結果的 Chat 形狀外衣（只保證 `.id` / `.choices[0].message.content` / `.usage.*`）。"""

    def __init__(self, resp: Any) -> None:
        self.id = getattr(resp, "id", None)
        usage = getattr(resp, "usage", None)
        self.usage = _Usage(usage) if usage is not None else None
        self.choices = [_Choice(extract_text(resp))]


def extract_text(resp: Any) -> str:
    """抽出模型正文：優先 SDK 的 `output_text`，否則手動走 `output` 只收 output_text part。

    ⚠️ 這是本模組最危險的一步：thinking 開啟時 Ark 的 `output` 首項是 reasoning item，混進去會把
    思考過程餵給 `client._loads_lenient` → 解析失敗回 None → `chat_json` 回 `{}` → 上層補預設值，
    **判決結果靜默劣化而不報錯**。故手動路徑嚴格只收 `type == "output_text"`，且有 output 卻抽不到
    正文時明確 warning（寧可吵，也不要靜默）。
    """
    text = getattr(resp, "output_text", None)
    if text:
        return str(text)
    items = getattr(resp, "output", None) or []
    parts = [
        str(getattr(part, "text", "") or "")
        for item in items
        for part in (getattr(item, "content", None) or [])
        if getattr(part, "type", "") == "output_text"
    ]
    if items and not parts:
        _log.warning(
            "Responses 回應抽不到 output_text part（output 型別＝%s）",
            [getattr(i, "type", "?") for i in items],
        )
    return "".join(parts)


def to_chat_shape(resp: Any) -> ChatShapeResponse:
    """Responses 結果 → Chat 形狀外衣（單一入口，供 client._complete 呼叫）。"""
    return ChatShapeResponse(resp)


# ── 串流形狀還原 ─────────────────────────────────────────────────────────────────────────
# 消費端（prompt_debug/prompt_reviser 的 stream_frames）只讀 chunk.usage 與
# chunk.choices[].delta.content，並在 finally 呼叫 stream.close()。


class _StreamChunk:
    """一個 Chat Completions chunk 的等價物（只帶消費端會讀的兩個面向）。"""

    def __init__(self, *, content: str | None = None, usage: Any = None) -> None:
        self.choices = [_Choice(content)] if content is not None else []
        self.usage = usage


class ChatShapeStream:
    """Responses 事件流 → Chat chunk 流。

    Ark 實測會吐 12 種事件，其中**有兩條 delta 流**：`response.reasoning_summary_text.delta`
    （思考摘要）與 `response.output_text.delta`（正文）。只轉發後者——思考摘要串進去會污染 JSON
    導致解析失敗（同 `extract_text` 的靜默劣化風險）。

    `response.completed` 攜帶完整 usage，轉成「choices 為空、只帶 usage」的末 chunk，語義對齊
    Chat Completions 的 `stream_options.include_usage`。
    """

    def __init__(self, stream: Any) -> None:
        self._stream = stream

    def __iter__(self):
        for event in self._stream:
            kind = getattr(event, "type", "")
            if kind == "response.output_text.delta":
                delta = getattr(event, "delta", None)
                if delta:
                    yield _StreamChunk(content=str(delta))
            elif kind == "response.completed":
                usage = getattr(getattr(event, "response", None), "usage", None)
                if usage is not None:
                    yield _StreamChunk(usage=_Usage(usage))
            # 其餘事件（reasoning_summary_*／output_item.*／content_part.*／in_progress）一律忽略

    def close(self) -> None:
        """對齊 Chat Completions Stream 的 close（消費端 finally 以 hasattr 判斷後呼叫）。"""
        closer = getattr(self._stream, "close", None)
        if callable(closer):
            closer()


def to_chat_stream(stream: Any) -> ChatShapeStream:
    """Responses 串流 → Chat chunk 流（單一入口，供 client._complete 呼叫）。"""
    return ChatShapeStream(stream)

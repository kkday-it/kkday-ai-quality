"""Chat Completions ↔ Responses API 翻譯層與回應/串流 adapter 單測（無需真 LLM key）。

mock 物件**刻意只給 Responses 的欄位名**（`input_tokens` 而非 `prompt_tokens`、`output_text`
而非 `choices`）——adapter 少映射一個欄位就會是 AttributeError 大聲失敗，而不是靜默拿到 0。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.judge.llm import responses_api

_SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}}
_CHAT_STRICT = {
    "type": "json_schema",
    "json_schema": {"name": "r", "strict": True, "schema": _SCHEMA},
}


# ── 請求翻譯 ─────────────────────────────────────────────────────────────────────────────
def test_json_schema_lifts_name_strict_schema_one_level() -> None:
    """response_format.json_schema → text.format，name/strict/schema 提升一層。"""
    out = responses_api.to_responses_kwargs({"messages": [], "response_format": _CHAT_STRICT})
    assert out["text"]["format"] == {
        "type": "json_schema",
        "name": "r",
        "strict": True,
        "schema": _SCHEMA,
    }
    assert "response_format" not in out


def test_json_object_maps_to_text_format_type() -> None:
    out = responses_api.to_responses_kwargs({"response_format": {"type": "json_object"}})
    assert out["text"] == {"format": {"type": "json_object"}}


def test_messages_becomes_input() -> None:
    msgs = [{"role": "user", "content": "hi"}]
    assert responses_api.to_responses_kwargs({"messages": msgs})["input"] == msgs


def test_reasoning_effort_must_be_nested_not_flat() -> None:
    """Ark 對扁平 `reasoning_effort` 回 400 unknown field（實測）——必須巢狀成 reasoning.effort。"""
    out = responses_api.to_responses_kwargs({"reasoning_effort": "high"})
    assert out["reasoning"] == {"effort": "high"}
    assert "reasoning_effort" not in out


def test_max_tokens_renamed_and_thinking_passthrough() -> None:
    out = responses_api.to_responses_kwargs(
        {"max_tokens": 64, "extra_body": {"thinking": {"type": "disabled"}}, "model": "m"}
    )
    assert out["max_output_tokens"] == 64 and "max_tokens" not in out
    assert out["extra_body"] == {"thinking": {"type": "disabled"}}  # Ark 原生開關原樣送
    assert out["model"] == "m"


def test_wire_marker_is_stripped_and_input_not_mutated() -> None:
    src = {responses_api.WIRE_API_KEY: "responses", "model": "m"}
    out = responses_api.to_responses_kwargs(src)
    assert responses_api.WIRE_API_KEY not in out
    assert responses_api.WIRE_API_KEY in src  # 入參不被改動


def test_stream_via_wrong_entrypoint_raises() -> None:
    with pytest.raises(ValueError):
        responses_api.to_responses_kwargs({"stream": True})


# ── 回應還原 ─────────────────────────────────────────────────────────────────────────────
def _responses_result(text='{"ok":true}', output=None):
    """只帶 Responses 欄位名的回應（刻意不給 choices / prompt_tokens）。"""
    usage = SimpleNamespace(
        input_tokens=184,
        output_tokens=40,
        total_tokens=224,
        input_tokens_details=SimpleNamespace(cached_tokens=12),
        output_tokens_details=SimpleNamespace(reasoning_tokens=35),
    )
    return SimpleNamespace(id="resp-1", output_text=text, output=output or [], usage=usage)


def test_usage_field_names_mapped_to_chat_shape() -> None:
    """input_tokens→prompt_tokens 等四個計數逐欄映射——抽錯不報錯，只會讓消耗看板慢慢歪掉。"""
    u = responses_api.to_chat_shape(_responses_result()).usage
    assert (u.prompt_tokens, u.completion_tokens, u.total_tokens) == (184, 40, 224)
    assert u.prompt_tokens_details.cached_tokens == 12
    assert u.completion_tokens_details.reasoning_tokens == 35


def test_id_and_content_readable_in_chat_shape() -> None:
    shaped = responses_api.to_chat_shape(_responses_result())
    assert shaped.id == "resp-1"  # 跑批落 jsonl 的 request_id 讀這個
    assert shaped.choices[0].message.content == '{"ok":true}'


def test_extract_text_skips_reasoning_items() -> None:
    """thinking 開啟時 output 首項是 reasoning item——混進正文會讓 JSON 解析失敗、靜默劣化。"""
    resp = _responses_result(
        text=None,
        output=[
            SimpleNamespace(
                type="reasoning",
                content=[SimpleNamespace(type="reasoning_text", text="我先想一下…")],
            ),
            SimpleNamespace(
                type="message", content=[SimpleNamespace(type="output_text", text='{"ok":true}')]
            ),
        ],
    )
    assert responses_api.extract_text(resp) == '{"ok":true}'


def test_extract_text_empty_output_is_safe() -> None:
    assert responses_api.extract_text(SimpleNamespace(output_text=None, output=[])) == ""


def test_missing_usage_yields_none_not_crash() -> None:
    resp = SimpleNamespace(id="x", output_text="{}", output=[], usage=None)
    assert responses_api.to_chat_shape(resp).usage is None


# ── 串流還原 ─────────────────────────────────────────────────────────────────────────────
def _event(kind, **kw):
    return SimpleNamespace(type=kind, **kw)


class _FakeStream:
    def __init__(self, events):
        self._events = events
        self.closed = False

    def __iter__(self):
        return iter(self._events)

    def close(self):
        self.closed = True


def test_stream_forwards_only_output_text_delta() -> None:
    """Ark 有**兩條** delta 流；思考摘要串進正文會污染 JSON——只轉發 output_text.delta。"""
    stream = _FakeStream(
        [
            _event("response.in_progress"),
            _event("response.reasoning_summary_text.delta", delta="思考中…"),
            _event("response.output_text.delta", delta='{"ok"'),
            _event("response.reasoning_summary_text.delta", delta="還在想…"),
            _event("response.output_text.delta", delta=":true}"),
            _event("response.output_item.done"),
        ]
    )
    texts = [c.choices[0].delta.content for c in responses_api.to_chat_stream(stream) if c.choices]
    assert "".join(texts) == '{"ok":true}'  # 思考摘要一個字都沒混進來


def test_stream_completed_event_carries_usage_as_final_chunk() -> None:
    """response.completed → 「choices 空、只帶 usage」的末 chunk，語義對齊 include_usage。"""
    usage = SimpleNamespace(
        input_tokens=10,
        output_tokens=5,
        total_tokens=15,
        input_tokens_details=SimpleNamespace(cached_tokens=0),
        output_tokens_details=SimpleNamespace(reasoning_tokens=2),
    )
    stream = _FakeStream(
        [
            _event("response.output_text.delta", delta="{}"),
            _event("response.completed", response=SimpleNamespace(usage=usage)),
        ]
    )
    chunks = list(responses_api.to_chat_stream(stream))
    assert chunks[-1].choices == [] and chunks[-1].usage.prompt_tokens == 10
    assert chunks[-1].usage.completion_tokens_details.reasoning_tokens == 2


def test_stream_close_is_forwarded() -> None:
    """消費端 finally 以 hasattr 判斷後呼叫 close()，必須傳導到底層串流。"""
    stream = _FakeStream([])
    responses_api.to_chat_stream(stream).close()
    assert stream.closed

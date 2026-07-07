"""③ OpenAI Batch API 封裝純函式測試（build_batch_line / build_batch_jsonl）：離線，不呼叫 OpenAI。"""

import json

from app.judge import batch_api


def test_build_batch_line_shape():
    """組成 OpenAI Batch JSONL request 標準形狀（custom_id/method/url/body messages）。"""
    line = batch_api.build_batch_line("rec_1", "gpt-5-nano", "sys", "usr")
    assert line["custom_id"] == "rec_1"
    assert line["method"] == "POST"
    assert line["url"] == "/v1/chat/completions"
    assert line["body"]["model"] == "gpt-5-nano"
    assert line["body"]["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "usr"},
    ]
    assert "response_format" not in line["body"]


def test_build_batch_line_with_schema():
    """schema 傳入 → 走 Structured Outputs（json_schema strict），與即時判決同格式。"""
    line = batch_api.build_batch_line("x", "m", "s", "u", schema={"type": "object"})
    rf = line["body"]["response_format"]
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["strict"] is True
    assert rf["json_schema"]["schema"] == {"type": "object"}


def test_build_batch_jsonl_roundtrip():
    """多行 → JSONL（每行一 JSON·可逐行 parse 回）。"""
    lines = [batch_api.build_batch_line(f"id{i}", "m", "s", "u") for i in range(3)]
    jsonl = batch_api.build_batch_jsonl(lines)
    parsed = [json.loads(row) for row in jsonl.splitlines()]
    assert len(parsed) == 3
    assert [p["custom_id"] for p in parsed] == ["id0", "id1", "id2"]

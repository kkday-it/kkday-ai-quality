"""kklog 結構化日誌（logging_setup）契約測試。

鎖三件事：①timestamp 逐筆即時（Filebeat 停收坑的迴歸鎖）②access log 排除健康檢查路徑
③X-Request-Id middleware 生成/沿用/回填。
"""

from __future__ import annotations

import json
import logging

from app.core.logging_setup import (
    ExcludeHealthPathFilter,
    KklogJsonFormatter,
)


def _record(msg: str, created: float) -> logging.LogRecord:
    """手工構造 LogRecord 並鎖定 created（模擬不同時刻產生的兩筆記錄）。"""
    r = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=(),
        exc_info=None,
    )
    r.created = created
    return r


def test_timestamp_is_per_record_not_cached() -> None:
    """同一 formatter 對不同 created 的記錄須產生不同 @timestamp——

    Filebeat 依 timestamp 判斷服務是否停止輸出，若 formatter 快取啟動時刻會被誤判停收
    （公司 Python 服務實測坑，KB1/1182892046）。此測試直接鎖定「不得快取」。
    """
    f = KklogJsonFormatter()
    t1 = json.loads(f.format(_record("a", 1_000_000.0)))["@timestamp"]
    t2 = json.loads(f.format(_record("b", 1_000_060.0)))["@timestamp"]
    assert t1 != t2


def test_format_is_single_line_json_with_contract_fields() -> None:
    """輸出為單行合法 JSON 且含 kklog 契約欄位（log_type/level/message/tracing/request）。"""
    out = KklogJsonFormatter().format(_record("hello 中文", 1_000_000.0))
    assert "\n" not in out
    payload = json.loads(out)
    assert payload["message"] == "hello 中文"
    assert payload["level"] == "INFO"
    assert payload["log_type"]  # 讀 config.env.log_type，非空即可（實際名稱與 DevOps 對齊）
    assert "trace_id" in payload["tracing"] and "uuid" in payload["request"]


def test_exception_field_included() -> None:
    """帶 exc_info 的記錄須附 exception 欄（stack trace 進 Kibana 可查）。"""
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        r = logging.LogRecord(
            name="t",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="err",
            args=(),
            exc_info=sys.exc_info(),
        )
    payload = json.loads(KklogJsonFormatter().format(r))
    assert "ValueError: boom" in payload["exception"]


def test_access_filter_excludes_health_path() -> None:
    """/api/status（k8s probe 每 10s 打）不得進 access log；一般路徑照常放行。"""
    flt = ExcludeHealthPathFilter()
    assert flt.filter(_record('GET /api/status HTTP/1.1" 200', 0.0)) is False
    assert flt.filter(_record('GET /api/problems HTTP/1.1" 200', 0.0)) is True


def test_request_id_header_roundtrip(temp_db) -> None:
    """middleware：無上游 id 時自生成回填；有上游 X-Request-Id 時沿用（跨 service 關聯）。"""
    from fastapi.testclient import TestClient

    from app.api.main import app

    with TestClient(app) as client:
        r = client.get("/api/status")
        assert r.headers.get("x-request-id")  # 自生成
        r2 = client.get("/api/status", headers={"X-Request-Id": "upstream-123"})
        assert r2.headers.get("x-request-id") == "upstream-123"  # 沿用上游

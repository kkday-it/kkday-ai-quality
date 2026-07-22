"""kklog 結構化 stdout 日誌（公司 Kibana/Filebeat 契約對齊）。

單檔收斂三件事：
- `KklogJsonFormatter`：全部 log（app + uvicorn）輸出單行 JSON，欄位對齊 new-kklog index
  慣例（@timestamp / log_type / level / message / tracing.trace_id / request.uuid）。
- `RequestContextMiddleware`：每請求生成（或沿用上游）X-Request-Id，經 contextvar 注入
  日誌並回填 response header——跨 service / 前後端關聯鍵。
- `configure_logging()`：dictConfig 覆寫 root + uvicorn 三支 logger；access log 排除
  `/api/status`（k8s probe 每 10s 打一次，不排除會灌爆 Kibana）。

⚠️ 已知坑（公司 Python 服務實測，KB1/1182892046）：timestamp 必須是「記錄當下」時間——
Filebeat 依 timestamp 判斷服務是否還有新 log，若 formatter 快取啟動時刻會被誤判停止收集。
本檔一律用 record.created（logging 於每筆記錄產生當下寫入）換算，並有迴歸測試鎖定。

已知限制：uvicorn.access 記錄發生在 protocol 層（非請求 task context），拿不到 contextvar
→ access log 的 request.uuid 恆空；app 內 log（router/judge 各模組 logger）正常帶值。
dev `--reload` 的 reloader 父進程在本配置載入前印的前兩行維持 uvicorn 原格式（prod 無 reloader）。
"""

from __future__ import annotations

import json
import logging
import logging.config
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone

from starlette.middleware.base import BaseHTTPMiddleware

from app.core import config

# 當前請求的關聯鍵（middleware 寫入、formatter 讀取；背景 thread 經 copy_context 快照帶入）
_request_uuid_var: ContextVar[str] = ContextVar("request_uuid", default="")
_trace_id_var: ContextVar[str] = ContextVar("trace_id", default="")

# access log 排除路徑（probe/scrape 噪音；健康檢查契約端點與 Prometheus metrics 見 main.py）
_ACCESS_LOG_EXCLUDE = ("/api/status", "/metrics")


def get_request_uuid() -> str:
    """當前請求的 request.uuid（無請求上下文回空字串）。"""
    return _request_uuid_var.get()


def get_trace_id() -> str:
    """當前請求的 tracing.trace_id（未接公司 tracing SDK 前恆空；接入時 middleware 填值即可）。"""
    return _trace_id_var.get()


class KklogJsonFormatter(logging.Formatter):
    """kklog 契約 JSON formatter：一筆 record → 一行 JSON（stdout，供 Filebeat 收集）。

    timestamp 一律由 record.created 逐筆換算（見模組 docstring 的 Filebeat 坑），
    禁止在 __init__ 快取任何時間值。
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "@timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "log_type": config.env.log_type,
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "tracing": {"trace_id": get_trace_id()},
            "request": {"uuid": get_request_uuid()},
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


class ExcludeHealthPathFilter(logging.Filter):
    """uvicorn.access 過濾器：健康檢查路徑不進日誌流（probe 每 10s 一發，屬純噪音）。

    比對 getMessage() 字串而非 record.args tuple 結構——uvicorn access record 的
    args 形狀跨版本有變動，訊息字串比對較穩。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return not any(path in msg for path in _ACCESS_LOG_EXCLUDE)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """每請求關聯鍵：沿用上游 X-Request-Id（跨 service 傳遞）或自行生成，回填 response header。"""

    async def dispatch(self, request, call_next):  # noqa: ANN001, ANN201  Starlette 介面簽名
        req_uuid = request.headers.get("x-request-id") or str(uuid.uuid4())
        token = _request_uuid_var.set(req_uuid)
        try:
            response = await call_next(request)
        finally:
            _request_uuid_var.reset(token)
        response.headers["X-Request-Id"] = req_uuid
        return response


def configure_logging() -> None:
    """套用 kklog JSON 日誌到 root + uvicorn 三支 logger（冪等，重複呼叫覆寫同配置）。

    呼叫時機＝app.api.main 模組載入時：uvicorn 的 Config 先跑過自己的 dictConfig，
    app import 在其後，故本配置後蓋前、可靠覆寫 uvicorn handler。
    disable_existing_loggers=False：保留各模組已建立的 logger 層級結構，僅換 handler/formatter。
    """
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {"kklog": {"()": KklogJsonFormatter}},
            "filters": {"exclude_health": {"()": ExcludeHealthPathFilter}},
            "handlers": {
                "stdout": {
                    "class": "logging.StreamHandler",
                    "stream": "ext://sys.stdout",
                    "formatter": "kklog",
                },
                "stdout_access": {
                    "class": "logging.StreamHandler",
                    "stream": "ext://sys.stdout",
                    "formatter": "kklog",
                    "filters": ["exclude_health"],
                },
            },
            "root": {"level": "INFO", "handlers": ["stdout"]},
            "loggers": {
                # uvicorn 三支顯式接管（propagate=False 防雙寫 root）
                "uvicorn": {"level": "INFO", "handlers": ["stdout"], "propagate": False},
                "uvicorn.error": {"level": "INFO", "handlers": ["stdout"], "propagate": False},
                "uvicorn.access": {
                    "level": "INFO",
                    "handlers": ["stdout_access"],
                    "propagate": False,
                },
            },
        }
    )

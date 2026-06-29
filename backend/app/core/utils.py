"""跨模組共用純函式（無副作用、無 app 內部依賴）。"""

from __future__ import annotations

from datetime import datetime, timezone


def now_iso() -> str:
    """本地時區當下時間 ISO8601（秒級）。各模組 updated_at/created_at 統一用此（取代散落的 _now）。"""
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

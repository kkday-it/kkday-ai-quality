"""repository 共用：ORM row → dict（供 router 直接回 JSON）。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import inspect


def row_to_dict(obj: Any) -> dict[str, Any]:
    """ORM 物件 → 欄位 dict（FastAPI 可直接序列化；datetime 自動轉 ISO）。"""
    return {c.key: getattr(obj, c.key) for c in inspect(obj).mapper.column_attrs}

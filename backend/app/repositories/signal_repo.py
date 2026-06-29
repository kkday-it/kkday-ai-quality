"""signal 讀取（聚合統計類）。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Signal
from app.repositories._common import row_to_dict


def list_signals(
    session: Session,
    source: str | None = None,
    prod_oid: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """列出 signal（可依 source / prod_oid 過濾）。"""
    stmt = select(Signal)
    if source:
        stmt = stmt.where(Signal.source == source)
    if prod_oid:
        stmt = stmt.where(Signal.prod_oid == prod_oid)
    stmt = stmt.limit(limit).offset(offset)
    return [row_to_dict(r) for r in session.execute(stmt).scalars().all()]

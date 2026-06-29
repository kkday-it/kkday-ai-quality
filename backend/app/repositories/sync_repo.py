"""同步狀態讀取（sync_run / dead_letter；可觀測性）。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DeadLetter, SyncRun
from app.repositories._common import row_to_dict


def list_runs(session: Session, source: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """列出同步批次（新到舊）。"""
    stmt = select(SyncRun)
    if source:
        stmt = stmt.where(SyncRun.source == source)
    stmt = stmt.order_by(SyncRun.started_at.desc()).limit(limit)
    return [row_to_dict(r) for r in session.execute(stmt).scalars().all()]


def list_dead_letters(
    session: Session, source: str | None = None, limit: int = 100
) -> list[dict[str, Any]]:
    """列出攝取失敗紀錄（排查用）。"""
    stmt = select(DeadLetter)
    if source:
        stmt = stmt.where(DeadLetter.source == source)
    stmt = stmt.order_by(DeadLetter.created_at.desc()).limit(limit)
    return [row_to_dict(r) for r in session.execute(stmt).scalars().all()]

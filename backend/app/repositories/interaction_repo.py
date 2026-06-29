"""interaction / message 讀取（純資料存取，不含業務）。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Interaction, Message
from app.repositories._common import row_to_dict


def list_interactions(
    session: Session,
    prod_oid: str | None = None,
    source: str | None = None,
    link_status: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """列出 interaction（可依 prod_oid / source / link_status 過濾），新到舊。"""
    stmt = select(Interaction)
    if prod_oid:
        stmt = stmt.where(Interaction.prod_oid == prod_oid)
    if source:
        stmt = stmt.where(Interaction.source == source)
    if link_status:
        stmt = stmt.where(Interaction.link_status == link_status)
    stmt = stmt.order_by(Interaction.occurred_at.desc().nullslast()).limit(limit).offset(offset)
    return [row_to_dict(r) for r in session.execute(stmt).scalars().all()]


def get_messages(session: Session, interaction_id: str) -> list[dict[str, Any]]:
    """取某 interaction 的訊息（依 seq 排序）。"""
    stmt = (
        select(Message)
        .where(Message.interaction_id == interaction_id)
        .order_by(Message.seq.asc().nullslast())
    )
    return [row_to_dict(r) for r in session.execute(stmt).scalars().all()]

"""product / package 讀取（校驗基準數據）。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Package, Product
from app.repositories._common import row_to_dict


def list_products(session: Session, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
    """列出商品。"""
    stmt = select(Product).order_by(Product.prod_oid).limit(limit).offset(offset)
    return [row_to_dict(r) for r in session.execute(stmt).scalars().all()]


def get_product(session: Session, prod_oid: str) -> dict[str, Any] | None:
    """取單一商品（含 packages）。"""
    obj = session.get(Product, prod_oid)
    if obj is None:
        return None
    data = row_to_dict(obj)
    pkgs = session.execute(select(Package).where(Package.prod_oid == prod_oid)).scalars().all()
    data["packages"] = [row_to_dict(p) for p in pkgs]
    return data

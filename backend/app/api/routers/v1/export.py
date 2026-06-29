"""導出端點（第三階段實作；第一階段接口預留，回 501）。

一鍵導出逐條 finding + 操作後數據（finding_action 軌跡）。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/export", tags=["export"])


@router.get("/findings")
def export_findings(
    prod_oid: str | None = None, status: str | None = None, fmt: str = "csv"
) -> dict:
    """導出逐條 finding + 操作後數據。第三階段實作。"""
    raise HTTPException(status_code=501, detail="導出層為第三階段，尚未實作（接口已預留）")

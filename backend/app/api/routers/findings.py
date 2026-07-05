"""判決結果端點（列表 / 商品清單 / 人工狀態 / 真值標註）；全路徑自帶 /api。"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core import db

router = APIRouter()


@router.get("/api/findings")
def get_findings(
    prod_oid: str | None = None,
    dimension: str | None = None,
) -> list[dict]:
    """列出判決結果（可依 prod_oid / dimension 過濾；下鑽用）。"""
    return db.list_findings(prod_oid, dimension)


@router.get("/api/products")
def get_products() -> list[dict]:
    """有 finding 的商品清單（PM 單品頁下拉）。"""
    return db.list_products()


class StatusIn(BaseModel):
    # 人工只可改這三態；new / auto_confirmed 由系統設定（初判 + G1 自動確認路由）。非法值 Pydantic 自動回 422。
    status: Literal["confirmed", "dismissed", "fixed"]


@router.patch("/api/findings/{finding_id}/status")
def patch_finding_status(finding_id: str, body: StatusIn) -> dict:
    """更新 Finding 狀態（出口A 確認/忽略/已修）。"""
    if not db.update_finding_status(finding_id, body.status):
        raise HTTPException(status_code=404, detail="finding not found")
    return {"finding_id": finding_id, "status": body.status}


class TrueLabelIn(BaseModel):
    """人工標註真值分類：true_label 存正確歸因（如 L1 域 code）；None/空＝清除標註。"""

    true_label: str | None = None


@router.patch("/api/findings/{finding_id}/true_label")
def patch_finding_true_label(finding_id: str, body: TrueLabelIn) -> dict:
    """人工標註單筆歸因的真值分類 true_label（供準確率評估 / 未來微調）。重判依 finding_id 保留。"""
    if not db.update_finding_true_label(finding_id, body.true_label):
        raise HTTPException(status_code=404, detail="finding not found")
    return {"finding_id": finding_id, "true_label": body.true_label}

"""判決結果人工動作端點（人工狀態 / 真值標註 + 把關 / 歸因備註 / 級聯樹）；全路徑自帶 /api。"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core import auth, db
from app.core.permissions import permission_keys, require_permission

router = APIRouter()


class StatusIn(BaseModel):
    # 人工可設 confirmed / dismissed，或 new＝撤銷判決回待處理；auto_confirmed 僅系統設定
    # （G1 自動確認路由）。fixed 已撤除（死狀態，migration a3b9d5e72f04 併入 confirmed）。
    status: Literal["confirmed", "dismissed", "new"]


class BatchStatusIn(BaseModel):
    """批量初判：對多則評論（source_id 清單）的全部歸因設定 status（同值列冪等跳過）。"""

    source: str
    source_ids: list[str]
    status: Literal["confirmed", "dismissed", "new"]


# 必須註冊於 /api/findings/{finding_id}/status 之前：FastAPI 依註冊序匹配，
# 置後會被參數路由攔截（finding_id="batch"）。
@router.patch("/api/findings/batch/verdict")
def batch_patch_finding_status(
    body: BatchStatusIn,
    user: dict = Depends(require_permission(permission_keys.FINDING_REVIEW_UPDATE)),
) -> dict:
    """批量更新判決狀態（勾選多則評論一鍵確認/忽略/撤銷）；需 finding.review.update 權限。

    單一交易內逐筆 diff（已是目標狀態者跳過），實際轉移按評論聚合記入歸因歷史。
    """
    if not body.source_ids:
        raise HTTPException(status_code=422, detail="source_ids 不可為空")
    actor = user.get("email") or user.get("user_id") or "unknown"
    result = db.batch_update_finding_status(body.source, body.source_ids, body.status, actor=actor)
    return {"status": body.status, **result}


@router.patch("/api/findings/{finding_id}/verdict")
def patch_finding_status(
    finding_id: str,
    body: StatusIn,
    user: dict = Depends(require_permission(permission_keys.FINDING_REVIEW_UPDATE)),
) -> dict:
    """更新 Finding 狀態（確認/駁回/撤銷判決）；需 finding.review.update 權限。

    同值冪等 no-op；實際轉移記操作者/時間 audit + 評論級歷史（attribution_history kind='verdict'）。
    """
    actor = user.get("email") or user.get("user_id") or "unknown"
    if not db.update_finding_status(finding_id, body.status, actor=actor):
        raise HTTPException(status_code=404, detail="finding not found")
    return {"finding_id": finding_id, "status": body.status}


@router.get("/api/findings/taxonomy-cascade")
def get_taxonomy_cascade(_: dict = Depends(auth.get_current_user)) -> list[dict]:
    """歸因分類級聯樹（L1→L2 巢狀 {value,label,children}）——供前端歸因分類 cascader
    （歸因列表篩選選域與面向）。"""
    from app.core.judge_config import ai_judge

    return ai_judge.cascade_tree()


class NoteIn(BaseModel):
    """新增歸因備註：content 為備註內容（備註人由登入身分帶入、時間由 DB 補）。"""

    content: str


@router.get("/api/findings/{finding_id}/notes")
def get_finding_notes(finding_id: str, user: dict = Depends(auth.get_current_user)) -> list[dict]:
    """列某條歸因的備註歷史（新到舊：id / 備註人 / 備註時間 / 備註內容）；需登入（內部 QC 討論內容）。"""
    return db.list_finding_notes(finding_id)


@router.post("/api/findings/{finding_id}/notes")
def add_finding_note(
    finding_id: str, body: NoteIn, user: dict = Depends(auth.get_current_user)
) -> dict:
    """為某條歸因新增一則備註（append-only）；備註人＝登入 email、備註時間由 DB 補。"""
    content = (body.content or "").strip()
    if not content:
        raise HTTPException(status_code=422, detail="備註內容不可為空")
    if db.get_finding(finding_id) is None:
        raise HTTPException(status_code=404, detail="finding not found")
    return db.add_finding_note(
        finding_id, author=user.get("email") or user.get("user_id") or "unknown", content=content
    )


@router.get("/api/attribution-history")
def get_attribution_history(
    source: str, source_id: str, user: dict = Depends(auth.get_current_user)
) -> list[dict]:
    """某則評論的歸因歷史時間軸（新到舊；judgment 快照 / status 判決轉移 / note 備註混排）。"""
    return db.list_attribution_history(source, source_id)


@router.get("/api/attribution-history/models")
def get_prejudge_models(user: dict = Depends(auth.get_current_user)) -> list[str]:
    """歷來實際初判過的模型清單（attributions 當前 ∪ attribution_history 快照 distinct）。

    供「初判模型」篩選與導出「輸出結果版本」下拉選項；字母序、stub 排最後。
    """
    return db.list_prejudge_models()


class HistoryNoteIn(BaseModel):
    """新增評論級備註（歸因歷史時間軸內；與 finding 級備註 finding_notes 並存）。"""

    source: str
    source_id: str
    content: str


@router.post("/api/attribution-history/notes")
def add_attribution_history_note(
    body: HistoryNoteIn, user: dict = Depends(auth.get_current_user)
) -> dict:
    """為某則評論新增一則評論級備註（kind='note'，append-only）；備註人＝登入 email。"""
    content = (body.content or "").strip()
    if not content:
        raise HTTPException(status_code=422, detail="備註內容不可為空")
    return db.add_history_note(
        body.source,
        body.source_id,
        author=user.get("email") or user.get("user_id") or "unknown",
        content=content,
    )

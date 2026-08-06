"""歸因分類級聯樹 + 評論級歸因歷史（時間軸／備註／模型清單）端點；全路徑自帶 /api。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core import auth, db

router = APIRouter()


@router.get("/api/findings/taxonomy-cascade")
def get_taxonomy_cascade(_: dict = Depends(auth.get_current_user)) -> list[dict]:
    """歸因分類級聯樹（L1→L2 巢狀 {value,label,children}）——供前端歸因分類 cascader
    （歸因列表篩選選域與面向）。"""
    from app.core.judge_config import ai_judge

    return ai_judge.cascade_tree()


class NoteIn(BaseModel):
    """新增歸因備註：content 為備註內容（備註人由登入身分帶入、時間由 DB 補）。"""

    content: str


@router.get("/api/attribution-history")
def get_attribution_history(
    source: str, source_id: str, user: dict = Depends(auth.get_current_user)
) -> list[dict]:
    """某則評論的歸因歷史時間軸（舊到新；prejudge 快照 / note 備註 / failure 初判失敗混排）。"""
    return db.list_attribution_history(source, source_id)


@router.get("/api/attribution-history/models")
def get_prejudge_models(user: dict = Depends(auth.get_current_user)) -> list[str]:
    """歷來實際初判過的模型清單（attributions 當前 ∪ attribution_history 快照 distinct）。

    供「初判模型」篩選與導出「輸出結果版本」下拉選項；字母序、stub 排最後。
    """
    return db.list_prejudge_models()


class HistoryNoteIn(BaseModel):
    """新增評論級備註（寫入歸因歷史時間軸的 kind='note' 事件）。"""

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
        author=auth.actor(user),
        content=content,
    )

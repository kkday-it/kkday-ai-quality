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


class NoteIn(BaseModel):
    """新增備註。

    `l1_code` / `l2_code` 同時給＝**面向備註**（掛在某個 L1›L2 面向上），同時省略＝**整則備註**。
    只給其中一個回 422——半個面向鍵無法解讀。
    """

    source: str
    source_id: str
    note_type: str
    content: str
    l1_code: str | None = None
    l2_code: str | None = None


@router.get("/api/attribution-notes/types")
def list_note_types(_: dict = Depends(auth.get_current_user)) -> list[dict]:
    """可選的互動類型（attribution_dimension_master 的 note_type 軸，僅啟用項）。

    值域走值域主檔而非寫死：業務自己在後台就能增減「已聯繫供應商」「待跟進」這類類型，
    不必回來找工程師。該表無刪除端點（停用走 is_active=false），歷史備註引用的 code 不會消失。
    """
    return db.active_note_types()


@router.get("/api/attribution-notes")
def list_attribution_notes(
    source: str, source_id: str, _: dict = Depends(auth.get_current_user)
) -> list[dict]:
    """某則反饋的全部備註（舊到新）。時間軸另有合併版，見 GET /api/attribution-history。"""
    return db.list_notes(source, source_id)


@router.post("/api/attribution-notes")
def add_attribution_note(body: NoteIn, user: dict = Depends(auth.get_current_user)) -> dict:
    """新增一則備註（append-only）；留言者＝登入 email（無 SSO 時為 system）。"""
    try:
        return db.add_note(
            body.source,
            body.source_id,
            note_type=body.note_type,
            content=body.content,
            author=auth.actor(user),
            l1_code=body.l1_code,
            l2_code=body.l2_code,
        )
    except db.NoteError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

"""人工糾正歸因端點（改 / 增 / 標記誤判 / 還原 / 複審確認）；全路徑自帶 /api。

**全 POST**：本專案零個 PUT/PATCH/DELETE 端點，mutation 一律 POST（既有慣例，見
`findings.py` 的 `POST /api/attribution-notes`）。

業務規則全在 `db.corrections`，本層只做兩件事：Pydantic 形狀校驗、把 `CorrectionError.code`
對映成 HTTP 狀態碼。這樣 db 模組可被腳本與測試直呼而不必吞 FastAPI 依賴。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core import auth, db
from app.core.permissions import permission_keys, require_permission

router = APIRouter()

# CorrectionError.code → HTTP 狀態碼。invalid 用 422（與 Pydantic 校驗失敗同碼），
# conflict 用 409（自然鍵已被佔用／狀態不允許），not_found 用 404（含跨反饋越權）。
_STATUS_BY_CODE = {"not_found": 404, "conflict": 409, "invalid": 422}


def _handle(fn, *args, **kwargs) -> dict:
    """執行 db.corrections 動作並把業務錯誤轉成 HTTP 錯誤。"""
    try:
        return fn(*args, **kwargs)
    except db.CorrectionError as e:
        raise HTTPException(status_code=_STATUS_BY_CODE.get(e.code, 400), detail=e.detail) from e


class _Target(BaseModel):
    """指向單一歸因的座標（source + source_id 用來擋跨反饋越權改值）。"""

    source: str
    source_id: str
    attribution_oid: int


class CorrectIn(_Target):
    """修改既有歸因：只帶要改的欄，未帶的沿用現值。"""

    changes: dict = Field(default_factory=dict)
    reason: str


class CreateIn(BaseModel):
    """人工新增 AI 漏掉的歸因（l2_code / polarity / summary 必填，l1 由 L2 推導）。"""

    source: str
    source_id: str
    values: dict = Field(default_factory=dict)
    reason: str


class ReasonIn(_Target):
    """只需要理由的動作（標記誤判 / 還原）。"""

    reason: str


class ConfirmIn(_Target):
    """複審確認 AI 判對；confirmed_fields＝人看過且確認正確的欄位（沒列的欄不計分）。"""

    confirmed_fields: list[str] = Field(default_factory=list)
    note: str = ""


@router.get("/api/attributions/correction-policy")
def get_correction_policy(_: dict = Depends(auth.get_current_user)) -> dict:
    """糾正政策（可改欄白名單 + 理由長度門檻）——前端糾正抽屜據此決定表單長什麼樣。

    與後端寫入白名單同讀 `config/ai_judge/correction.json`，避免兩邊漂移。
    """
    from app.core.db import corrections as _c

    cfg = _c._cfg()
    return {
        "editable_fields": cfg["editable_fields"],
        "reason_min_length": cfg["reason_min_length"],
        "reason_max_length": cfg["reason_max_length"],
    }


@router.get("/api/attributions")
def list_attributions(
    source: str,
    source_id: str,
    _: dict = Depends(auth.get_current_user),
) -> dict:
    """糾正工作台的資料源：一則反饋的全部歸因（`live` / `deleted` 兩個陣列）+ 託管狀態 + 待審數。

    讀取級權限（`get_current_user`，同本 router 其他 GET）：能看列表就能看這則反饋的歸因全貌；
    寫入動作各自有 `attribution.correction.manage` / `attribution.review` 把關。

    ⚠️ 這是全專案第二個刻意回傳 tombstone 的路徑，已登記於 `tests/test_tombstone_invisible.py`
    的「刻意的例外」段落。
    """
    return db.list_record_attributions(source, source_id)


@router.post("/api/attributions/correct")
def correct(
    body: CorrectIn,
    user: dict = Depends(require_permission(permission_keys.ATTRIBUTION_CORRECTION_MANAGE)),
) -> dict:
    """修改一條 AI 歸因的分類／傾向（該反饋自此進入人工託管，重新初判不再覆蓋）。"""
    return _handle(
        db.correct_attribution,
        body.source,
        body.source_id,
        body.attribution_oid,
        changes=body.changes,
        reason=body.reason,
        author=auth.actor(user),
    )


@router.post("/api/attributions/create")
def create(
    body: CreateIn,
    user: dict = Depends(require_permission(permission_keys.ATTRIBUTION_CORRECTION_MANAGE)),
) -> dict:
    """人工新增一條 AI 漏掉的歸因。"""
    return _handle(
        db.create_attribution,
        body.source,
        body.source_id,
        values=body.values,
        reason=body.reason,
        author=auth.actor(user),
    )


@router.post("/api/attributions/delete")
def delete(
    body: ReasonIn,
    user: dict = Depends(require_permission(permission_keys.ATTRIBUTION_CORRECTION_MANAGE)),
) -> dict:
    """標記一條歸因為 AI 誤判（tombstone；列保留以佔住自然鍵，防重新初判悄悄復活）。"""
    return _handle(
        db.delete_attribution,
        body.source,
        body.source_id,
        body.attribution_oid,
        reason=body.reason,
        author=auth.actor(user),
    )


@router.post("/api/attributions/restore")
def restore(
    body: ReasonIn,
    user: dict = Depends(require_permission(permission_keys.ATTRIBUTION_CORRECTION_MANAGE)),
) -> dict:
    """還原被標記為誤判的歸因。"""
    return _handle(
        db.restore_attribution,
        body.source,
        body.source_id,
        body.attribution_oid,
        reason=body.reason,
        author=auth.actor(user),
    )


class SwapIn(BaseModel):
    """互換同一則反饋內兩條歸因的 L1/L2 面向。"""

    source: str
    source_id: str
    attribution_oid_a: int
    attribution_oid_b: int
    reason: str


@router.post("/api/attributions/swap")
def swap(
    body: SwapIn,
    user: dict = Depends(require_permission(permission_keys.ATTRIBUTION_CORRECTION_MANAGE)),
) -> dict:
    """互換兩條歸因的面向（單一交易，兩條同時生效）。

    存在的理由：「AI 把兩個面向的內容寫反了」在逐條提交下是死結——先改哪一條都會撞上另一條佔著
    的面向（`_assert_slot_free` 連 tombstone 都算佔用）。沒有這個端點，使用者只能走
    「先改成第三個暫時面向 → 換另一條 → 再改回來」的三步，中間態是假資料。

    實作靠自然鍵已是 DEFERRABLE 約束（migration a3e58d21c9f4），交易內延後檢查即可，
    不需要塞暫存假值繞路。詳見 `db.corrections.swap_attribution_slots`。
    """
    return _handle(
        db.swap_attribution_slots,
        body.source,
        body.source_id,
        oid_a=body.attribution_oid_a,
        oid_b=body.attribution_oid_b,
        reason=body.reason,
        author=auth.actor(user),
    )


@router.post("/api/attributions/confirm")
def confirm(
    body: ConfirmIn,
    user: dict = Depends(require_permission(permission_keys.ATTRIBUTION_REVIEW)),
) -> dict:
    """複審確認 AI 判對了——待複審的出口。**不**讓該反饋進入人工託管（沒改值就不需要保護）。"""
    return _handle(
        db.confirm_attribution,
        body.source,
        body.source_id,
        body.attribution_oid,
        confirmed_fields=body.confirmed_fields,
        note=body.note,
        author=auth.actor(user),
    )


class ResolveIn(BaseModel):
    """採納／駁回待審建議（batch_id 用來擋兩人同時處理造成的覆蓋）。"""

    source: str
    source_id: str
    batch_id: str
    decisions: list[dict] = Field(default_factory=list)
    reason: str = ""


@router.get("/api/attribution-suggestions")
def get_suggestions(source: str, source_id: str, _: dict = Depends(auth.get_current_user)) -> dict:
    """某則反饋的待審 LLM 建議（人工現值 vs LLM 新值，兩側同形供對比 UI 共用渲染）。"""
    return db.list_pending_suggestions(source, source_id)


@router.post("/api/attribution-suggestions/resolve")
def resolve(
    body: ResolveIn,
    user: dict = Depends(require_permission(permission_keys.ATTRIBUTION_REVIEW)),
) -> dict:
    """採納／駁回建議 → {applied, rejected, remaining}。batch 過期回 409 要求重新載入。"""
    try:
        return db.resolve_suggestions(
            body.source,
            body.source_id,
            body.batch_id,
            body.decisions,
            reason=body.reason,
            author=auth.actor(user),
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


class DimensionItemIn(BaseModel):
    """單一值域項（upsert on (dimension_code, item_code)）。"""

    dimension_code: str
    item_code: str
    item_label: str
    item_desc: str | None = None
    sort_order: int = 0
    is_active: bool = True


class ReorderIn(BaseModel):
    """某軸的顯示順序（拖曳排序後整份送出）。"""

    dimension_code: str
    item_codes: list[str] = Field(default_factory=list)


@router.get("/api/attribution-dimensions")
def get_dimensions(
    include_inactive: bool = False, _: dict = Depends(auth.get_current_user)
) -> dict:
    """值域主檔（目前僅備註互動類型 note_type 一軸）；預設只回可選項。"""
    return db.list_dimensions(include_inactive=include_inactive)


@router.post("/api/attribution-dimensions/save")
def save_dimension(
    body: DimensionItemIn,
    user: dict = Depends(require_permission(permission_keys.ATTRIBUTION_DIMENSION_MANAGE)),
) -> dict:
    """新增或更新單一值域項。**無刪除端點**——停用走 is_active=false，硬刪會讓歷史判決顯示空白。"""
    try:
        return db.save_dimension_item(body.model_dump(), author=auth.actor(user))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.post("/api/attribution-dimensions/reorder")
def reorder_dimensions(
    body: ReorderIn,
    user: dict = Depends(require_permission(permission_keys.ATTRIBUTION_DIMENSION_MANAGE)),
) -> dict:
    """重寫某軸的顯示順序 → {updated}。"""
    try:
        return {
            "updated": db.reorder_dimension(
                body.dimension_code, body.item_codes, author=auth.actor(user)
            )
        }
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

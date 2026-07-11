"""判決結果人工動作端點（人工狀態 / 真值標註 + 把關 / 歸因備註 / 級聯樹）；全路徑自帶 /api。"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core import auth, db
from app.core.permissions import permission_keys, require_permission

router = APIRouter()


class StatusIn(BaseModel):
    # 人工可設 confirmed / dismissed，或 new＝撤銷覆核回待處理；auto_confirmed 僅系統設定
    # （G1 自動確認路由）。fixed 已撤除（死狀態，migration a3b9d5e72f04 併入 confirmed）。
    status: Literal["confirmed", "dismissed", "new"]


class BatchStatusIn(BaseModel):
    """批量覆核：對多則評論（source_id 清單）的全部歸因設定 status（同值列冪等跳過）。"""

    source: str
    source_ids: list[str]
    status: Literal["confirmed", "dismissed", "new"]


# 必須註冊於 /api/findings/{finding_id}/status 之前：FastAPI 依註冊序匹配，
# 置後會被參數路由攔截（finding_id="batch"）。
@router.patch("/api/findings/batch/status")
def batch_patch_finding_status(
    body: BatchStatusIn,
    user: dict = Depends(require_permission(permission_keys.FINDING_REVIEW_UPDATE)),
) -> dict:
    """批量更新覆核狀態（勾選多則評論一鍵確認/忽略/撤銷）；需 finding.review.update 權限。

    單一交易內逐筆 diff（已是目標狀態者跳過），實際轉移按評論聚合記入判決歷史。
    """
    if not body.source_ids:
        raise HTTPException(status_code=422, detail="source_ids 不可為空")
    actor = user.get("email") or user.get("user_id") or "unknown"
    result = db.batch_update_finding_status(body.source, body.source_ids, body.status, actor=actor)
    return {"status": body.status, **result}


@router.patch("/api/findings/{finding_id}/status")
def patch_finding_status(
    finding_id: str,
    body: StatusIn,
    user: dict = Depends(require_permission(permission_keys.FINDING_REVIEW_UPDATE)),
) -> dict:
    """更新 Finding 狀態（確認/忽略/撤銷覆核）；需 finding.review.update 權限。

    同值冪等 no-op；實際轉移記操作者/時間 audit + 評論級歷史（judgment_history kind='status'）。
    """
    actor = user.get("email") or user.get("user_id") or "unknown"
    if not db.update_finding_status(finding_id, body.status, actor=actor):
        raise HTTPException(status_code=404, detail="finding not found")
    return {"finding_id": finding_id, "status": body.status}


@router.get("/api/findings/taxonomy-cascade")
def get_taxonomy_cascade() -> list[dict]:
    """歸因分類級聯樹（L1→L2→L3 巢狀 {value,label,children}）——供標真值 a-cascader 選擇。"""
    from app.core.judge_config import ai_judge

    return ai_judge.cascade_tree()


def _review_text(finding: dict) -> str:
    """由判決列取回反饋原文（複用判決管線的 source_mapping 正規化 + _text_of），供 LLM 重判評分。"""
    from app.core.judge_config import source_mapping
    from app.judge import prejudge

    src = finding.get("source") or ""
    sid = finding.get("source_id") or ""
    rows = db.get_items_by_ids([sid], src) if sid else []
    if not rows:
        return ""
    canon = source_mapping.normalize_row(src, rows[0]) if src in source_mapping.sources() else {}
    item = {**rows[0], "content": canon.get("content") or "", "raw": rows[0]}
    return prejudge._text_of(item)


def _true_label_cfg() -> dict:
    """標真值把關旋鈕（judgment.true_label；含把關閾值 + 評分模型）。"""
    from app.core.db import _shared

    return _shared.read_judgment_config().get("true_label", {})


def _evaluate_model(cfg_judgment: dict) -> str:
    """標真值評分模型：judgment.true_label.evaluate_model → 回退 prejudge.stage1_model → gpt-5-mini。"""
    tl = cfg_judgment.get("true_label", {})
    return (
        tl.get("evaluate_model")
        or cfg_judgment.get("prejudge", {}).get("stage1_model")
        or "gpt-5-mini"
    )


class EvaluateIn(BaseModel):
    """標真值評分請求：級聯選出的真值 code（L1 域 code 或任一層 C-code）。"""

    proposed_label: str


@router.post("/api/findings/{finding_id}/true_label/evaluate")
def evaluate_true_label(
    finding_id: str, body: EvaluateIn, user: dict = Depends(auth.get_current_user)
) -> dict:
    """標真值把關：LLM 對『人工提議真值 vs 反饋原文』評分，回與原判信心對比 + 是否需填理由（防亂標）。"""
    from app.core import settings as app_settings
    from app.core.db import _shared
    from app.judge import prejudge
    from app.judge.llm import client as _client

    finding = db.get_finding(finding_id)
    if finding is None:
        raise HTTPException(status_code=404, detail="finding not found")
    # 於 handler 同一 thread 注入該 user 的 active LLM 設定（否則 score_true_label 走預設/stub·用錯 model）；
    # 並設用量落庫情境（true_label 階段即時單列 insert）——修此端點原本漏設 settings/usage 的缺口。
    app_settings.set_current(
        app_settings.effective_llm_dict(app_settings.load_settings(user["user_id"]))
    )
    _client.set_usage_context(
        {"source": finding.get("source"), "source_id": finding.get("source_id")}
    )
    text = _review_text(finding)
    if not text:
        raise HTTPException(status_code=422, detail="無法取得反饋原文，無法評分")
    cfg = _shared.read_judgment_config()
    scored = prejudge.score_true_label(text, body.proposed_label, _evaluate_model(cfg))
    original = finding.get("conf_value")
    llm_conf = scored["confidence"]
    threshold = float(cfg.get("true_label", {}).get("reason_required_drop", 0.15))
    drop = (original - llm_conf) if original is not None else 0.0
    return {
        "finding_id": finding_id,
        "proposed_label": body.proposed_label,
        "llm_confidence": llm_conf,
        "original_confidence": original,
        "delta": (llm_conf - original) if original is not None else None,
        "reason_llm": scored["reason"],
        "reason_required": original is not None and drop > threshold,
        "threshold": threshold,
    }


class TrueLabelIn(BaseModel):
    """人工標註真值分類（級聯選出的葉 code）；None/空＝清除。reason/llm_conf 為把關 audit（見 evaluate）。"""

    true_label: str | None = None
    reason: str | None = None  # 修改理由（LLM 信心明顯下降時前端要求填）
    llm_conf: float | None = None  # 標註當下 LLM 對真值的契合信心（供 audit + 後端把關）


@router.patch("/api/findings/{finding_id}/true_label")
def patch_finding_true_label(
    finding_id: str,
    body: TrueLabelIn,
    user: dict = Depends(require_permission(permission_keys.FINDING_TRUE_LABEL_UPDATE)),
) -> dict:
    """人工標註單筆歸因真值 true_label（+把關 audit：理由 + LLM 信心）；需 finding.true-label.update 權限。重判依 finding_id 保留。

    後端把關（防繞過 UI）：設真值且帶 llm_conf 時，若『原判信心 − llm_conf』> 閾值卻未附理由 → 422。
    """
    finding = db.get_finding(finding_id)
    if finding is None:
        raise HTTPException(status_code=404, detail="finding not found")
    setting = bool((body.true_label or "").strip())
    if setting and body.llm_conf is not None:
        original = finding.get("conf_value")
        threshold = float(_true_label_cfg().get("reason_required_drop", 0.15))
        if (
            original is not None
            and (original - body.llm_conf) > threshold
            and not (body.reason or "").strip()
        ):
            raise HTTPException(
                status_code=422, detail="LLM 對此真值信心明顯偏低，需填寫修改理由才能標註"
            )
    db.update_finding_true_label(
        finding_id,
        body.true_label,
        reason=body.reason,
        llm_conf=body.llm_conf,
        actor=user.get("email") or user.get("user_id") or "unknown",
    )
    return {"finding_id": finding_id, "true_label": body.true_label}


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


@router.get("/api/judgment-history")
def get_judgment_history(
    source: str, source_id: str, user: dict = Depends(auth.get_current_user)
) -> list[dict]:
    """某則評論的判決歷史時間軸（新到舊；judgment 快照 / status 覆核轉移 / note 備註混排）。"""
    return db.list_judgment_history(source, source_id)


class HistoryNoteIn(BaseModel):
    """新增評論級備註（判決歷史時間軸內；與 finding 級備註 finding_notes 並存）。"""

    source: str
    source_id: str
    content: str


@router.post("/api/judgment-history/notes")
def add_judgment_history_note(
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

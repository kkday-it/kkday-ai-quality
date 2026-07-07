"""判決結果端點（列表 / 商品清單 / 人工狀態 / 真值標註 / 歸因備註）；全路徑自帶 /api。"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core import auth, db

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
def patch_finding_true_label(finding_id: str, body: TrueLabelIn) -> dict:
    """人工標註單筆歸因真值 true_label（+把關 audit：理由 + LLM 信心）。重判依 finding_id 保留。

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
        finding_id, body.true_label, reason=body.reason, llm_conf=body.llm_conf
    )
    return {"finding_id": finding_id, "true_label": body.true_label}


class NoteIn(BaseModel):
    """新增歸因備註：content 為備註內容（備註人由登入身分帶入、時間由 DB 補）。"""

    content: str


@router.get("/api/findings/{finding_id}/notes")
def get_finding_notes(finding_id: str) -> list[dict]:
    """列某條歸因的備註歷史（新到舊：id / 備註人 / 備註時間 / 備註內容）。"""
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

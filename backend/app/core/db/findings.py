"""判決結果（judgments）CRUD：寫入 / 整組替換 / 讀取原始判決列 + 商品清單。"""

from __future__ import annotations

import logging

from sqlalchemy import and_, func, select
from sqlalchemy import delete as sa_delete
from sqlalchemy import insert as sa_insert
from sqlalchemy import update as sa_update

from app.core.db import tables as T
from app.core.db._shared import attribution_dto
from app.core.schema import TicketFinding

_log = logging.getLogger(__name__)


def _finding_values(f: TicketFinding, source: str) -> dict:
    """TicketFinding → judgments typed 欄位 dict（全 typed 欄，無 JSONB blob）。

    關聯鍵（source/source_id/prod_oid/dimension）+ 人工覆核軸（status/created_at/needs_review）
    於此補齊；判決 payload 17 欄由 f.to_columns() 攤出（polarity/l1_code…/conf_value/summary…）。
    殘留/legacy 欄不入庫。true_label 由 replace_source_findings 的 preserve 邏輯補（非首寫）。
    """
    return {
        "finding_id": f.finding_id,
        "source": source,
        "source_id": f.ticket_id,  # prejudge 設 ticket_id = 特徵 id（source_id）
        "prod_oid": f.prod_oid,  # ProductDetail / list_products 下鑽用
        "dimension": f.dimension,  # ProductDetail 內容/非內容過濾用
        "status": f.status,
        "created_at": f.created_at,
        "needs_review": bool(f.needs_review),  # 人審佇列篩選
        **f.to_columns(),
    }


def insert_finding(f: TicketFinding, source: str) -> None:
    """寫入判決結果（冪等：finding_id 重複則覆蓋）。source 定表、f.ticket_id 為特徵 id（source_id）。"""
    with T.get_engine().begin() as c:
        c.execute(T.upsert(T.judgments, _finding_values(f, source), ["finding_id"]))


def replace_source_findings(source: str, source_id: str, findings: list[TicketFinding]) -> int:
    """整組替換某來源列的所有歸因（1:N；刪 (source, source_id) 舊列 → 插新列），保留人工覆核軸。

    多歸因下一個來源列對應多筆 judgments（每域一筆）；重判以最新結果整組替換舊列（冪等），非逐筆
    upsert——否則舊域殘留孤兒列。刪除前撈各列 true_label + status 依 finding_id 回填（同域重判
    finding_id 不變＝人工覆核結果保留）：
    - true_label：人工標註真值，非空即保留。
    - status（G2）：僅保留**人工**覆核結果（confirmed/dismissed/fixed），避免重判把已處理歸因打回；
      new 與 **auto_confirmed（G1 系統自動確認）** 不保留——交由新判決 `_route_status` 依最新 tier+stage
      重新路由（重判後信心可能變、系統狀態應重算，非沿用舊自動確認）。
    判決引擎（prejudge_batch._work_one）全 5 來源統一走此。

    Args:
        source: 來源 code。
        source_id: 該來源列特徵 id（product_reviews→rec_oid…）。
        findings: 判決結果清單（to_findings 產出，≥1 筆）。

    Returns:
        寫入的歸因列數。
    """
    if not source_id:
        return 0
    jg = T.judgments
    key = and_(jg.c.source == source, jg.c.source_id == source_id)
    with T.get_engine().begin() as c:
        # FOR UPDATE 鎖住舊列：關閉「讀快照 → 之後才 DELETE/INSERT」之間的 TOCTOU 視窗
        # （批量重判長跑期間，並發的 update_finding_status/true_label 若插隊，本次會用舊快照覆蓋掉人工操作）。
        preserved = {
            r.finding_id: {
                "true_label": r.true_label,
                "true_label_reason": r.true_label_reason,
                "true_label_conf": r.true_label_conf,
                "status": r.status,
            }
            for r in c.execute(
                select(
                    jg.c.finding_id, jg.c.true_label, jg.c.true_label_reason, jg.c.true_label_conf, jg.c.status
                )
                .where(key)
                .with_for_update()
            )
        }
        new_ids = {f.finding_id for f in findings}
        # 審計：舊列有人工覆核/真值、但新判決不再產出該域（finding_id 無承接）→ 靜默隨整組刪除消失，留 log 可追。
        for fid, old in preserved.items():
            if fid not in new_ids and (old["status"] in ("confirmed", "dismissed", "fixed") or old["true_label"]):
                _log.warning(
                    "重判丟棄含人工覆核的舊歸因列 finding_id=%s status=%s true_label=%s（新判決不再產出此域）",
                    fid, old["status"], old["true_label"],
                )
        c.execute(sa_delete(jg).where(key))
        for f in findings:
            values = _finding_values(f, source)
            old = preserved.get(f.finding_id)
            if old:
                if old["true_label"] is not None:  # 真值三軸（真值 + 把關理由 + LLM 信心）一併保留
                    values["true_label"] = old["true_label"]
                    values["true_label_reason"] = old["true_label_reason"]
                    values["true_label_conf"] = old["true_label_conf"]
                if old["status"] in ("confirmed", "dismissed", "fixed"):  # 僅保留人工覆核；new/auto_confirmed 重算
                    values["status"] = old["status"]
            c.execute(sa_insert(jg).values(**values))
    return len(findings)


def list_findings(
    prod_oid: str | None = None,
    dimension: str | None = None,
) -> list[dict]:
    """列出判決結果（可依 prod_oid / dimension 過濾），新到舊。data 還原為完整 Finding。"""
    stmt = select(T.judgments)
    for col, val in (
        (T.judgments.c.prod_oid, prod_oid),
        (T.judgments.c.dimension, dimension),
    ):
        if val:
            stmt = stmt.where(col == val)
    stmt = stmt.order_by(T.judgments.c.created_at.desc())
    out = []
    with T.get_engine().connect() as c:
        for r in c.execute(stmt).mappings():
            d = dict(r)
            # typed 欄 → 乾淨巢狀 DTO（FindingCard 消費巢狀 finding）
            d["finding"] = attribution_dto(d)
            out.append(d)
    return out


def list_products() -> list[dict]:
    """有 finding 的商品清單（PM 下拉用），依問題數排序。"""
    n = func.count().label("n")
    stmt = (
        select(T.judgments.c.prod_oid, n)
        .where(T.judgments.c.dimension != "non_content")
        .group_by(T.judgments.c.prod_oid)
        .order_by(n.desc())
    )
    with T.get_engine().connect() as c:
        return [dict(r) for r in c.execute(stmt).mappings()]


def update_finding_status(finding_id: str, status: str) -> bool:
    """更新單筆 Finding 狀態（confirmed/dismissed/fixed）。回傳是否命中。"""
    stmt = (
        sa_update(T.judgments).where(T.judgments.c.finding_id == finding_id).values(status=status)
    )
    with T.get_engine().begin() as c:
        return c.execute(stmt).rowcount > 0


def update_finding_true_label(
    finding_id: str, true_label: str | None, *, reason: str | None = None, llm_conf: float | None = None
) -> bool:
    """人工標註單筆 Finding 的真值分類 true_label（+把關 audit：修改理由 + LLM 契合信心）。回傳是否命中。

    true_label 存人工用級聯選出的葉 code；None/空字串清除標註（連帶清 reason/conf）。
    reason：LLM 對真值信心明顯下降時人工填的修改理由（防亂標）。llm_conf：標註當下 LLM 對該真值的契合信心。
    重判（replace_source_findings）依 finding_id 保留此三軸（真值 + 理由 + 信心）。
    """
    clearing = not (true_label or "").strip()
    stmt = (
        sa_update(T.judgments)
        .where(T.judgments.c.finding_id == finding_id)
        .values(
            true_label=None if clearing else true_label,
            true_label_reason=None if clearing else (reason or None),
            true_label_conf=None if clearing else llm_conf,
        )
    )
    with T.get_engine().begin() as c:
        return c.execute(stmt).rowcount > 0


def get_finding(finding_id: str) -> dict | None:
    """取單筆判決列（供標真值把關讀原判信心 / 來源定位）；不存在回 None。

    Returns:
        judgments 列 mapping（含 source/source_id/conf_value/l1_code/true_label…），或 None。
    """
    with T.get_engine().connect() as c:
        r = c.execute(select(T.judgments).where(T.judgments.c.finding_id == finding_id)).mappings().first()
        return dict(r) if r else None

"""判決結果（judgments）CRUD：寫入 / 整組替換 / 單筆讀取 / 人工狀態覆核 + 歸因備註。"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import and_, select
from sqlalchemy import delete as sa_delete
from sqlalchemy import insert as sa_insert
from sqlalchemy import update as sa_update

from app.core.db import judgment_history as _history
from app.core.db import tables as T
from app.core.schema import TicketFinding

_log = logging.getLogger(__name__)


def _finding_values(f: TicketFinding, source: str) -> dict:
    """TicketFinding → judgments typed 欄位 dict（全 typed 欄，無 JSONB blob）。

    關聯鍵（source/source_id/prod_oid）+ 人工覆核軸（status/created_at/needs_review）
    於此補齊；判決 payload 17 欄由 f.to_columns() 攤出（polarity/l1_code…/conf_value/summary…）。
    殘留/legacy 欄不入庫。
    """
    return {
        "finding_id": f.finding_id,
        "source": source,
        "source_id": f.ticket_id,  # prejudge 設 ticket_id = 特徵 id（source_id）
        "prod_oid": f.prod_oid,  # 商品維度關聯（歸因列表 prod_oid 篩選 / 概覽下鑽）
        "status": f.status,
        "created_at": f.created_at,
        "needs_review": bool(f.needs_review),  # 人審佇列篩選
        **f.to_columns(),
    }


def insert_finding(f: TicketFinding, source: str) -> None:
    """寫入判決結果（冪等：finding_id 重複則覆蓋）。source 定表、f.ticket_id 為特徵 id（source_id）。"""
    with T.get_engine().begin() as c:
        c.execute(T.upsert(T.judgments, _finding_values(f, source), ["finding_id"]))


def replace_source_findings(
    source: str,
    source_id: str,
    findings: list[TicketFinding],
    *,
    params: dict | None = None,
    job_id: str | None = None,
    triggered_by: str | None = None,
) -> int:
    """整組替換某來源列的所有歸因（1:N；刪 (source, source_id) 舊列 → 插新列），保留人工覆核軸。

    多歸因下一個來源列對應多筆 judgments（每(域,面向)一筆，同 L1 多 L2 面向並列）；重判以最新結果整組
    替換舊列（冪等），非逐筆 upsert——否則舊面向殘留孤兒列。刪除前撈各列 status 依 finding_id 回填
    （同(域,面向)重判 finding_id 不變＝人工覆核結果保留；L2 面向變動則視為新歸因、不承接舊覆核）：
    - status（G2）：僅保留**人工**覆核結果（confirmed/dismissed），避免重判把已處理歸因打回；
      new 與 **auto_confirmed（G1 系統自動確認）** 不保留——交由新判決 `_route_status` 依最新 tier+stage
      重新路由（重判後信心可能變、系統狀態應重算，非沿用舊自動確認）。
    判決引擎（prejudge_batch._work_one）全 5 來源統一走此。

    同交易尾端寫入評論級判決歷史（judgment_history kind='judgment'）：model+params+result_digest
    與最新一筆完全相同即 skip（全欄位嚴格去重）；與判決寫入同交易＋FOR UPDATE，防並發重判漏記/雙寫。

    Args:
        source: 來源 code。
        source_id: 該來源列特徵 id（product_reviews→rec_oid…）。
        findings: 判決結果清單（to_findings 產出，≥1 筆）。
        params: 判決參數精餾快照（model/voter_models/ensemble_sample_rate；歷史去重比對鍵之一）。
        job_id: 批次任務 id（歷史關聯 judgment_runs；直呼/測試可省略）。
        triggered_by: 觸發人（user email；歷史留痕）。

    Returns:
        寫入的歸因列數。
    """
    if not source_id:
        return 0
    jg = T.judgments
    key = and_(jg.c.source == source, jg.c.source_id == source_id)
    with T.get_engine().begin() as c:
        # FOR UPDATE 鎖住舊列：關閉「讀快照 → 之後才 DELETE/INSERT」之間的 TOCTOU 視窗
        # （批量重判長跑期間，並發的 update_finding_status 若插隊，本次會用舊快照覆蓋掉人工操作）。
        preserved = {
            r.finding_id: {
                "status": r.status,
                "status_updated_by": r.status_updated_by,
                "status_updated_at": r.status_updated_at,
            }
            for r in c.execute(
                select(
                    jg.c.finding_id,
                    jg.c.status,
                    jg.c.status_updated_by,
                    jg.c.status_updated_at,
                )
                .where(key)
                .with_for_update()
            )
        }
        new_ids = {f.finding_id for f in findings}
        # 審計：舊列有人工覆核、但新判決不再產出該域（finding_id 無承接）→ 靜默隨整組刪除消失，留 log 可追。
        for fid, old in preserved.items():
            if fid not in new_ids and old["status"] in ("confirmed", "dismissed"):
                _log.warning(
                    "重判丟棄含人工覆核的舊歸因列 finding_id=%s status=%s（新判決不再產出此域）",
                    fid,
                    old["status"],
                )
        c.execute(sa_delete(jg).where(key))
        snapshots: list[dict] = []
        for f in findings:
            values = _finding_values(f, source)
            # 快照於覆核軸回填前取（判決本體 SSOT；status 不入快照，見 snapshot_of）
            snapshots.append(_history.snapshot_of(values))
            old = preserved.get(f.finding_id)
            if old and old["status"] in (
                "confirmed",
                "dismissed",
            ):  # 僅保留人工覆核（含操作者/時間 audit）；new/auto_confirmed 重算
                values["status"] = old["status"]
                values["status_updated_by"] = old["status_updated_by"]
                values["status_updated_at"] = old["status_updated_at"]
            c.execute(sa_insert(jg).values(**values))
        if findings:
            # 評論級判決歷史（同交易；model/votes 每次 to_findings 呼叫內一致，取首筆即可）
            _history.insert_judgment_event(
                c,
                source,
                source_id,
                model=findings[0].model_used,
                model_votes=[
                    {"finding_id": f.finding_id, "votes": f.model_votes}
                    for f in findings
                    if f.model_votes
                ],
                params=params,
                attributions=snapshots,
                job_id=job_id,
                triggered_by=triggered_by,
            )
    return len(findings)


def update_finding_status(finding_id: str, status: str, *, actor: str | None = None) -> bool:
    """更新單筆 Finding 狀態（confirmed/dismissed/new＝撤銷覆核）+ 記操作者/時間 audit。回傳是否命中。

    同值冪等 no-op（不重寫 audit、不記歷史）；實際轉移時同交易寫入評論級歷史
    （judgment_history kind='status'，params 記 {to, changes:[{finding_id, from}]}）——
    status_updated_by/at 只留最後一次，完整轉移軌跡在歷史表。
    actor：操作者 email（登入身分）。
    """
    jg = T.judgments
    with T.get_engine().begin() as c:
        # FOR UPDATE 鎖行：讀現值→比對→更新+記史 原子化（防並發覆核互踩/重複記史）
        row = c.execute(
            select(jg.c.source, jg.c.source_id, jg.c.status)
            .where(jg.c.finding_id == finding_id)
            .with_for_update()
        ).first()
        if row is None:
            return False
        if (row.status or "new") == status:
            return True  # 同值冪等：不動 audit、不記歷史
        c.execute(
            sa_update(jg)
            .where(jg.c.finding_id == finding_id)
            .values(
                status=status,
                status_updated_by=actor,
                status_updated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            )
        )
        _history.insert_status_event(
            c,
            row.source,
            row.source_id,
            to_status=status,
            changes=[{"finding_id": finding_id, "from": row.status or "new"}],
            author=actor,
        )
        return True


def get_finding(finding_id: str) -> dict | None:
    """取單筆判決列（供覆核來源定位）；不存在回 None。

    Returns:
        judgments 列 mapping（含 source/source_id/conf_value/l1_code…），或 None。
    """
    with T.get_engine().connect() as c:
        r = (
            c.execute(select(T.judgments).where(T.judgments.c.finding_id == finding_id))
            .mappings()
            .first()
        )
        return dict(r) if r else None


def _note_row(r) -> dict:
    """finding_notes 列 → API dict（created_at ISO 字串）。"""
    return {
        "id": r["id"],
        "finding_id": r["finding_id"],
        "author": r["author"],
        "content": r["content"],
        "created_at": r["created_at"].isoformat() if r["created_at"] else None,
    }


def add_finding_note(finding_id: str, author: str, content: str) -> dict:
    """新增一則歸因備註（append-only 歷史）；回傳建立的備註（含 id / 時間）。"""
    ins = (
        sa_insert(T.finding_notes)
        .values(finding_id=finding_id, author=author, content=content)
        .returning(*T.finding_notes.c)
    )
    with T.get_engine().begin() as c:
        return _note_row(c.execute(ins).mappings().first())


def list_finding_notes(finding_id: str) -> list[dict]:
    """列某條歸因的備註歷史（新到舊：備註人 / 時間 / 內容）。"""
    stmt = (
        select(T.finding_notes)
        .where(T.finding_notes.c.finding_id == finding_id)
        .order_by(T.finding_notes.c.created_at.desc(), T.finding_notes.c.id.desc())
    )
    with T.get_engine().connect() as c:
        return [_note_row(r) for r in c.execute(stmt).mappings()]


def batch_update_finding_status(
    source: str, source_ids: list[str], status: str, *, actor: str | None = None
) -> dict:
    """批量覆核：對多則評論（source_id 清單）的**全部**歸因設定 status。回 {updated, finding_ids}。

    單一交易：FOR UPDATE 鎖住所有目標列 → 逐筆 diff（同值跳過＝冪等）→ 一次 UPDATE 有變更者 →
    按評論聚合寫入 kind='status' 歷史（一則評論一筆事件，params.changes 列各 finding 轉移）。
    選取鍵為 source_id（前端跨頁勾選即評論級），語意＝該評論全部歸因一併覆核。
    """
    ids = [str(s) for s in source_ids if s]
    if not ids:
        return {"updated": 0, "finding_ids": []}
    jg = T.judgments
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with T.get_engine().begin() as c:
        rows = c.execute(
            select(jg.c.finding_id, jg.c.source_id, jg.c.status)
            .where(and_(jg.c.source == source, jg.c.source_id.in_(ids)))
            .with_for_update()
        ).all()
        changed = [r for r in rows if (r.status or "new") != status]
        if not changed:
            return {"updated": 0, "finding_ids": []}
        c.execute(
            sa_update(jg)
            .where(jg.c.finding_id.in_([r.finding_id for r in changed]))
            .values(status=status, status_updated_by=actor, status_updated_at=now)
        )
        by_review: dict[str, list[dict]] = {}
        for r in changed:
            by_review.setdefault(r.source_id, []).append(
                {"finding_id": r.finding_id, "from": r.status or "new"}
            )
        for sid, changes in by_review.items():
            _history.insert_status_event(
                c, source, sid, to_status=status, changes=changes, author=actor
            )
    return {"updated": len(changed), "finding_ids": [r.finding_id for r in changed]}

"""初判結果（attributions）CRUD：寫入 / 整組替換 / 單筆讀取。

一則反饋有兩種託管狀態，`replace_source_findings` 依此分兩支：**AI 託管**（無任何人工痕跡）
重新初判即整組替換、行為與人工介入功能上線前逐欄相同；**人工託管**（任一列被人工新增／改值／
標記誤判）重新初判**完全不碰本表**，AI 結果轉入 `attribution_suggestion_lst` 待人工採納。
"""

from __future__ import annotations

import logging

from sqlalchemy import and_, select
from sqlalchemy import delete as sa_delete
from sqlalchemy import insert as sa_insert

from app.core.db import attribution_history as _history
from app.core.db import suggestions as _suggestions
from app.core.db import tables as T
from app.core.schema import TicketFinding

_log = logging.getLogger(__name__)


def _finding_values(f: TicketFinding, source: str) -> dict:
    """TicketFinding → attributions typed 欄位 dict（全 typed 欄，無 JSONB blob）。

    關聯鍵（source/source_id）+ 簿記欄（is_auto_accepted/created_at）於此補齊；
    初判 payload 17 欄由 f.to_columns() 攤出（polarity/l1_code…/conf_value/summary…）。
    殘留/legacy 欄不入庫。
    """
    return {
        "source": source,
        "source_id": f.ticket_id,  # prejudge 設 ticket_id = 特徵 id（source_id）
        "is_auto_accepted": bool(f.is_auto_accepted),  # G1 自動確認路由結果
        # create_date 已是 timestamptz：空字串非合法值，轉 None（由 DB 端留空）
        "created_at": f.created_at or None,
        **f.to_columns(),
    }


def replace_source_findings(
    source: str,
    source_id: str,
    findings: list[TicketFinding],
    *,
    params: dict | None = None,
    job_id: str | None = None,
    triggered_by: str | None = None,
) -> dict:
    """寫入一次初判的結果——**依託管狀態分兩支**（2026-08-06 起）。

    多歸因下一個來源列對應多筆 attributions（每(域,面向)一筆，同 L1 多 L2 面向並列）；重新初判以最新
    結果整組替換舊列（冪等），非逐筆 upsert——否則舊面向殘留孤兒列。

    - **AI 託管**（該反饋無任何人工痕跡）→ 整組替換，行為與人工介入功能上線前**逐欄相同**：
      刪 (source, source_id) 舊列 → 插新列（冪等，非逐筆 upsert，否則舊面向殘留孤兒列）。
    - **人工託管**（任一列被人工新增／改值／標記誤判）→ **一列都不寫** `attribution_tbl`，
      AI 結果整組轉入 `attribution_suggestion_lst` 待人工採納（見 db.suggestions 模組 docstring）。

    這條分岔讓「人工改分類後 AI 新結果撞自然鍵」在物理上不可能發生——人工託管下根本沒有寫入，
    故 `idx_attribution_tbl_unique01` 不必為此讓步。

    同交易尾端寫入評論級歸因歷史（attribution_history kind='prejudge'）：model+params+result_digest
    與最新一筆完全相同即 skip（全欄位嚴格去重）。

    Args:
        source: 來源 code。
        source_id: 該來源列特徵 id（reviews→rec_oid…）。
        findings: 判決結果清單（to_findings 產出，≥1 筆）。
        params: 初判參數精餾快照（model；歷史去重比對鍵之一）。
        job_id: 批次任務 id（歷史關聯 prejudge_runs；直呼/測試可省略）。
        triggered_by: 觸發人（user email；歷史留痕）。

    Returns:
        `{"mode": "replace"|"suggest", "written": 落庫歸因列數, "suggested": 產生的建議項數}`。
        （2026-08-06 由 `int` 改為 dict：呼叫端要能區分「跑了但轉建議」與「跑了且落庫」，
        否則使用者會覺得「我重判了但畫面沒變」是 bug。）
    """
    if not source_id:
        return {"mode": "replace", "written": 0, "suggested": 0}
    jg = T.attributions
    key = and_(jg.c.source == source, jg.c.source_id == source_id)
    with T.get_engine().begin() as c:
        # ── 人工託管分支：AI 結果不覆蓋現值，整組轉為待審建議 ──────────────────
        # 判定與寫入必須同交易（下方 begin() 區塊內），否則兩個並發重新初判可能同時讀到
        # 「還是 AI 託管」而雙雙走整組替換，把人工值輾過去。
        if _suggestions.is_human_managed(c, source, source_id):
            current = (
                c.execute(select(jg).where(key).order_by(jg.c.attribution_oid)).mappings().all()
            )
            proposed = [_finding_values(f, source) for f in findings]
            items = _suggestions.diff_findings([dict(r) for r in current], proposed)
            model = findings[0].model_used if findings else str((params or {}).get("model") or "")
            batch_id = _suggestions.write_suggestions(
                c, source, source_id, items, model=model, job_id=job_id, author=triggered_by or ""
            )
            _history.insert_manual_event(
                c,
                source,
                source_id,
                kind="suggestion",
                params={
                    "batch_id": batch_id,
                    "model": model,
                    "counts": {
                        t: sum(1 for i in items if i["change_type"] == t)
                        for t in ("replace", "add", "remove")
                    },
                },
                attributions=[_history.snapshot_of(v) for v in proposed],
                author=triggered_by or "",
            )
            return {"mode": "suggest", "written": 0, "suggested": len(items)}

        # ── AI 託管分支：與本次改動前**逐欄相同**的既有行為 ────────────────────
        c.execute(sa_delete(jg).where(key))
        snapshots: list[dict] = []
        for f in findings:
            values = _finding_values(f, source)
            snapshots.append(_history.snapshot_of(values))
            c.execute(sa_insert(jg).values(**values))
        # 評論級歸因歷史（同交易）：**空結果也記**——「這次初判把歸因清空了」與「跑了但結果一樣」
        # 都是使用者要在時間軸看到的事實，漏記會讓該次 job 在這則評論上完全查無痕跡。
        # model 取首筆（同一次 to_findings 內一致）；空結果無首筆可取，退回 job 級參數快照。
        _history.insert_prejudge_event(
            c,
            source,
            source_id,
            model=findings[0].model_used if findings else str((params or {}).get("model") or ""),
            params=params,
            attributions=snapshots,
            job_id=job_id,
            triggered_by=triggered_by,
        )
    return {"mode": "replace", "written": len(findings), "suggested": 0}

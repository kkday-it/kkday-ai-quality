"""初判結果（attributions）CRUD：寫入 / 整組替換 / 單筆讀取。

歸因列完全由初判產生，無人工可改欄位——重新初判即整組替換，不需承接任何舊值。
"""

from __future__ import annotations

import logging

from sqlalchemy import and_
from sqlalchemy import delete as sa_delete
from sqlalchemy import insert as sa_insert

from app.core.db import attribution_history as _history
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


def insert_finding(f: TicketFinding, source: str) -> None:
    """寫入初判結果（冪等：同 (來源, 評論, L1, L2) 重複則覆蓋）。

    衝突鍵＝該表真正的自然鍵四欄 (feedback_source_code, source_id, l1_code, l2_code)。
    """
    with T.get_engine().begin() as c:
        c.execute(
            T.upsert(
                T.attributions,
                _finding_values(f, source),
                ["source", "source_id", "l1_code", "l2_code"],
            )
        )


def replace_source_findings(
    source: str,
    source_id: str,
    findings: list[TicketFinding],
    *,
    params: dict | None = None,
    job_id: str | None = None,
    triggered_by: str | None = None,
) -> int:
    """整組替換某來源列的所有歸因（1:N；刪 (source, source_id) 舊列 → 插新列）。

    多歸因下一個來源列對應多筆 attributions（每(域,面向)一筆，同 L1 多 L2 面向並列）；重新初判以最新
    結果整組替換舊列（冪等），非逐筆 upsert——否則舊面向殘留孤兒列。

    整組替換是**無承接**的：歸因列沒有任何人工可改、需跨重判存活的欄位，故刪除前不撈舊值回填
    （評論級的人為輸入與歷次軌跡都由 attribution_history 承擔）。

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
        寫入的歸因列數。
    """
    if not source_id:
        return 0
    jg = T.attributions
    key = and_(jg.c.source == source, jg.c.source_id == source_id)
    with T.get_engine().begin() as c:
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
    return len(findings)

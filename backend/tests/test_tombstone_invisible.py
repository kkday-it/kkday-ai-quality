"""人工誤判 tombstone（`is_deleted=true`）在所有回傳歸因數字的路徑上都必須隱形。

**為什麼需要這支枚舉式測試**：tombstone 的過濾條件散落在多條查詢路徑上，漏掉任何一條的後果
**不是報錯，是統計悄悄變大**——列表少一筆、概覽多一筆，沒有任何人會發現。12 個查詢點中有 10 個
走 `_shared._jg_join_cond` / `_jg_exists` 兩個 chokepoint（改預設值即一次到位），另外 4 處
（`attribution.py` 的縱覽分支與 `ai_judge_overview_stats`、`attribution_history.list_prejudge_models`、
反向的 `prejudge_targets`）必須顯式處理，本測試就是它們的護欄。

⚠️ **日後每新增一個回傳歸因數字的端點，都要加進本檔的枚舉**——這是 tombstone 設計的永久維護稅，
沒有任何通用機制能自動抓到「你新寫了一條 SQL 卻忘了帶謂詞」。

**刻意的例外（只有兩個，各自有鎖定測試）**：

1. `prejudge_targets` 反向要求 tombstone **算已判過**（否則歸因全被標記誤判的反饋會被
   scope=unjudged 永遠重複撈取）→ `test_tombstone_counts_as_judged_for_prejudge_targets`。
   同一個「判過沒有」的口徑也套用在 `list_problems(judged=)` 與 `ai_judge_overview_stats`
   的 `judged_items` 上 → `test_tombstone_still_counts_as_judged_at_feedback_level`。
2. `db.list_record_attributions`（糾正工作台的讀取端點）**刻意回傳 tombstone**——「還原誤判」的
   入口只能長在看得到那些列的地方 → `test_workbench_endpoint_exposes_tombstones_separately`。
   它把 tombstone 收在獨立的 `deleted` 陣列，不混進 `live`，所以不會污染任何既有消費端。
"""

from __future__ import annotations

from sqlalchemy import update

from app.core import db
from app.core.db import tables as T
from app.core.schema import TicketFinding
from tests._factories import review_row

_SRC = "reviews"


def _finding(rec_oid: str, l1: str = "content", l2: str = "C-1-1") -> TicketFinding:
    """一筆對應 reviews 列的負向歸因。"""
    return TicketFinding(
        ticket_id=rec_oid,
        recommended_action="no_action",
        l1_domain_code=l1,
        l1_label=l1,
        l2_code=l2,
        l2_label=l2,
        polarity="negative",
        sentiment_score=1,
        summary={"zh-tw": "頁面資訊與現場不符"},
        model_used="gpt-5-mini",
    )


def _seed(rec_oid: str) -> None:
    """建一則反饋 + 一條歸因。"""
    db.insert_source_batch(_SRC, [review_row(rec_oid)])
    db.replace_source_findings(
        _SRC, rec_oid, [_finding(rec_oid)], params={"model": "gpt-5-mini"}, job_id="pj_test"
    )


def _mark_deleted(rec_oid: str) -> None:
    """把該反饋的歸因標記為人工誤判（模擬 correction API 的 delete op）。"""
    jg = T.attributions
    with T.get_engine().begin() as c:
        c.execute(
            update(jg)
            .where(jg.c.source == _SRC, jg.c.source_id == rec_oid)
            .values(is_deleted=True, correction_reason="AI 誤判：這是正向回饋")
        )


def _attribution_level_counts() -> dict[str, float]:
    """**歸因層**數字：問「現在有哪些歸因」，標記誤判後必須全部歸零（live 口徑）。

    ⚠️ **新增任何回傳歸因計數的端點時，必須加進這個 dict**——這是本檔的維護稅。
    2026-08-07 的實例：`ai_judge_overview_stats` 的 `content_items` 因為沒被枚舉到，
    漏掉 `live_attr_cond()` 而沒被任何測試攔下，導致 `content_share_pct` 變成
    「含 tombstone ÷ 不含 tombstone」的錯值。
    """
    stats = db.ai_judge_overview_stats()["totals"]
    return {
        "overview_attributed": db.attribution_overview(source=_SRC)["attributed"],
        "overview_all_sources": db.attribution_overview()["attributed"],
        "breakdown": sum(r["n"] for r in db.attribution_breakdown(_SRC, "content")["by_l2"]),
        "stats_attributed_rows": stats["attributed_rows"],
        "stats_content_items": stats["content_items"],
        "stats_content_share_pct": stats["content_share_pct"],
    }


def _feedback_level_judged() -> dict[str, float]:
    """**反饋層**判定：問「這則判過沒有」，標記誤判**不改變答案**（ever 口徑）。

    這組與上面那組是兩個不同的不變式，混在一起是 2026-08-07 之前的錯誤——當時
    `list_problems(judged=True)["total"]` 被當成「歸因數」的代理指標，但它其實是反饋數。
    """
    return {
        "list_problems_judged": db.list_problems(source=_SRC, judged=True)["total"],
        "stats_judged_items": db.ai_judge_overview_stats()["totals"]["judged_items"],
    }


def test_tombstone_invisible_across_all_attribution_readers(temp_db) -> None:
    """歸因層：每條回傳歸因數字的路徑，標記誤判後全部看不到它。

    先量測「有一條歸因」的基線，標記誤判後每條路徑都必須回到「零歸因」——不是比對硬編碼數字，
    而是比對同一路徑標記前後的差，這樣即使各路徑的口徑定義不同也不會誤判。
    """
    _seed("T1")

    before = _attribution_level_counts()
    assert all(v > 0 for v in before.values()), f"基線有路徑量不到歸因：{before}"

    _mark_deleted("T1")

    after = _attribution_level_counts()
    leaked = {k: v for k, v in after.items() if v != 0}
    assert not leaked, (
        f"這些路徑仍看得到人工標記為誤判的歸因：{leaked}（基線 {before}）。"
        f"該查詢沒走 _jg_join_cond / _jg_exists，需顯式補 live_attr_cond()。"
    )


def test_tombstone_still_counts_as_judged_at_feedback_level(temp_db) -> None:
    """反饋層：標記誤判**不會**把一則反饋變回「未初判」。

    三個畫面問的是同一件事，必須給同一個答案：列表的「已初判」篩選、概覽的「已初判進線」、
    批量初判的標的選取。2026-08-07 之前它們各說各話，使用者會看到
    「篩出未初判 1 筆，按下初判分類時目標數 0 筆」。
    """
    _seed("T3")
    before = _feedback_level_judged()
    assert all(v == 1 for v in before.values()), f"基線不成立：{before}"

    _mark_deleted("T3")

    after = _feedback_level_judged()
    regressed = {k: v for k, v in after.items() if v != 1}
    assert not regressed, (
        f"這些路徑把「歸因全被標記誤判」的反饋當成未初判：{regressed}。"
        f"「判過沒有」一律用 ever 口徑（include_deleted=True），與 prejudge_targets 同一把尺。"
    )
    assert db.list_problems(source=_SRC, judged=False)["total"] == 0
    (row,) = db.list_problems(source=_SRC, judged=True)["rows"]
    assert row["judge_state"] == "dismissed" and row["dismissed_count"] == 1, (
        "列上要看得出「判過但歸因全被標記誤判」，否則使用者只會看到一列空白"
    )


def test_content_share_pct_numerator_excludes_tombstone(temp_db) -> None:
    """`content_share_pct` ＝「有 content 主因的進線（live）」/「已初判進線（ever）」。

    兩者口徑刻意不同，但各自都對：分母問「判過沒有」（tombstone 算判過）、分子問「現在的主因是
    什麼」（人說判錯的不算）。2026-08-07 之前分子漏了 `live_attr_cond()`，於是被標記為誤判的歸因
    仍被算成「有 content 主因」——百分比虛高，而且**不會報錯**。

    用「兩則反饋、只標記其中一則」造出會露餡的局面：分子正確時掉到 1（50%），
    分子含 tombstone 時會停在 2（100%）。
    """
    _seed("S1")
    _seed("S2")
    assert db.ai_judge_overview_stats()["totals"]["content_share_pct"] == 100.0

    _mark_deleted("S1")

    totals = db.ai_judge_overview_stats()["totals"]
    assert totals["judged_items"] == 2, "分母應是 ever 口徑——兩則都判過（其中一則被標記誤判）"
    assert totals["content_items"] == 1, (
        "分子仍含 tombstone——`content_items` 漏了 live_attr_cond()，被人工判定為誤判的歸因"
        "不該再被算成「有 content 主因」"
    )
    assert totals["content_share_pct"] == 50.0


def test_tombstone_counts_as_judged_for_prejudge_targets(temp_db) -> None:
    """反向語義：tombstone 對初判標的選取而言**算判過**，不得被 scope=unjudged 重複撈取。

    這是全專案唯一刻意帶 `include_deleted=True` 的地方。若這條紅了，代表有人「順手統一」把
    `prejudge_targets` 的例外拿掉——後果是歸因全被標記誤判的反饋每次批量初判都被撈進來重跑，
    每次產生一批建議、每次被無視（無限重撈迴圈）。
    """
    _seed("T2")
    assert "T2" not in db.prejudge_target_ids(_SRC, None, stages=["unjudged"])

    _mark_deleted("T2")
    assert "T2" not in db.prejudge_target_ids(_SRC, None, stages=["unjudged"]), (
        "歸因被標記為 AI 誤判後，該反饋又被當成「未初判」撈進批量初判標的——"
        "prejudge_targets 的 include_deleted=True 例外被拿掉了"
    )


def test_workbench_endpoint_exposes_tombstones_separately(temp_db) -> None:
    """糾正工作台端點是**刻意**回傳 tombstone 的例外，但必須收在獨立陣列裡。

    兩件事要同時成立，少一件工作台就壞掉：
    - `deleted` 看得到它（否則使用者永遠還原不了被誤標的歸因）
    - `live` 看不到它（否則工作台會把已標記誤判的列當成有效歸因，計數與 cascader 佔用集全歪）
    """
    _seed("W1")
    before = db.list_record_attributions(_SRC, "W1")
    assert len(before["live"]) == 1 and before["deleted"] == []
    assert before["human_managed"] is False

    _mark_deleted("W1")

    after = db.list_record_attributions(_SRC, "W1")
    assert after["live"] == [], "tombstone 混進了 live 陣列"
    assert len(after["deleted"]) == 1, "工作台看不到 tombstone，就沒有還原的入口"
    assert after["human_managed"] is True, "標記誤判後該反饋應進入人工託管"
    # DTO 形狀未被動過：tombstone 的身分由「在哪個陣列」承載，不是靠列上多一個旗標
    assert "is_deleted" not in after["deleted"][0]
    assert "is_dismissed" not in after["deleted"][0]

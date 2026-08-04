"""wire 契約快照：凍結 DB 層各讀取函式回傳給 API 的 key 形狀與時間欄型別。

**存在的理由**：多個讀取函式目前以 `select(表)` 全欄直出（`prejudge_runs.list_prejudge_runs`、
`attribution_history.list_attribution_history`、`ingest.list_batches`、
`prompt_debug_reviews.fetch_prompt_debug_reviews`），DB 加一個欄就等於改一次 API 契約 —— 新增欄會
**自動**流到前端，而型別檢查與既有測試都攔不住。本檔把當下的形狀凍結成常數，使
「DB schema 演進」與「wire 契約變更」必須是兩個顯式動作。

**怎麼用**：改動 DB 欄或序列化邏輯後本檔若變紅，代表 wire 契約真的動了 —— 若是預期內的
契約變更，同輪更新下方常數並確認前端消費端；若非預期，那就是無意間外洩了內部欄位。

時間欄一律斷言為 `str`（ISO）：專案慣例是 datetime 欄在出 API 前轉 ISO 字串
（見 `prejudge_runs._serialize` / `attribution_history._history_row`），日後時間欄改型別時
這幾條斷言會逐一指出所有需要跟著改的序列化點。
"""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from app.core import db
from app.core.db._shared import attribution_dto

# ── 凍結的 wire 形狀（dotted path；巢狀 dict 展開，list 不展開）──────────────────
# 值＝該 key 允許的 Python 型別名；None 表示不約束型別（僅約束 key 存在）。

_PREJUDGE_RUN_WIRE = {
    "job_id": "str",
    "kind": "str",
    "rejudge": None,
    "source": None,
    "model": None,
    "params": "dict",  # JSONB 發起參數快照（動態 key，視為葉節點）
    "status": "str",
    "total": None,
    "processed": None,
    "ok": None,
    "failed": None,
    "total_tokens": None,
    "cost_usd": None,
    "triggered_by": None,
    "started_at": "str",  # datetime → ISO 字串
    "finished_at": None,  # 執行中為 None，終態為 ISO 字串
}

_ATTRIBUTION_HISTORY_WIRE = {
    "id": "int",
    "source": "str",
    "source_id": "str",
    "kind": "str",
    "model": None,
    "params": None,
    "attributions": None,
    # result_digest 已於 2026-08-04 移出 wire（內部去重鍵，前端零消費）
    "job_id": None,
    "triggered_by": None,
    "author": None,
    "content": None,
    "created_at": "str",
}

_BATCH_WIRE = {
    "batch_id": "str",
    "name": "str",
    "source": "str",
    "original_name": "str",
    "row_count": "int",
    "uploaded_at": "str",
    "note": "str",
}

# 歸因 DTO（`_shared.attribution_dto`）—— 前端 Attribution interface 的對應面。
# 這是全專案唯一有結構性巢狀的 wire 形狀，故需指定可展開的欄（其餘端點皆為扁平）。
_ATTRIBUTION_DTO_STRUCTURAL = frozenset({"l1", "l2", "confidence", "content"})

_ATTRIBUTION_DTO_WIRE = {
    # 身分鍵：原為 finding_id（(來源,評論,L1,L2) 四欄的字串編碼），2026-08-04 退場——
    # 它 100% 冗餘且實測 91% 的值與 feedback_source_code 互相矛盾。改由 serial PK 承擔。
    "attribution_oid": None,
    "polarity": None,
    "sentiment_score": None,
    "stage": None,
    "l1.code": None,
    "l1.label": None,
    "l2.code": None,
    "l2.label": None,
    "confidence.value": None,
    "confidence.raw": None,
    "confidence.tier": None,
    "content.summary": None,
    "content.summary_langs": "dict",  # 語系→摘要 map（動態 key，視為葉節點）
    "content.evidence": None,
    "content.action": None,
    "owner": None,
    "model": None,
    "is_primary": None,
    "is_auto_accepted": None,
}


def _shape(
    obj: Mapping, structural: frozenset[str] = frozenset(), prefix: str = ""
) -> dict[str, str | None]:
    """mapping → {dotted key: 型別名}；只對 `structural` 列名的欄展開巢狀。

    **為何要顯式列出可展開的欄**：wire 上的 dict 有兩種，混談會讓契約失去意義 ——
    ① 結構性巢狀（`l1`/`confidence`/`content`）：key 集合固定，是契約的一部分，要展開比對；
    ② JSONB 資料 payload（`params`/`ai_output`/`versions`/`summary_langs`）：key 由業務資料
    決定、隨夾具而變，展開只會讓契約隨測試資料浮動（實測：空 dict 展開後整個 key 直接消失）。
    後者一律視為葉節點，只鎖「這個 key 存在且是 dict」。

    值為 None 的欄位型別記為 None（測試端據此放寬型別斷言，因為 nullable 欄在夾具中
    可能就是空的）。
    """
    out: dict[str, str | None] = {}
    for k, v in obj.items():
        key = f"{prefix}{k}"
        if isinstance(v, Mapping) and key in structural:
            out.update(_shape(v, structural, prefix=f"{key}."))
        else:
            out[key] = None if v is None else type(v).__name__
    return out


def _assert_wire(
    actual: Mapping,
    expected: dict[str, str | None],
    label: str,
    structural: frozenset[str] = frozenset(),
) -> None:
    """比對實際 wire 形狀與凍結契約：key 集合須完全相同，且有約束的欄型別須相符。"""
    shape = _shape(actual, structural)
    missing = sorted(set(expected) - set(shape))
    extra = sorted(set(shape) - set(expected))
    assert not missing and not extra, (
        f"{label} 的 wire 契約已變動 —— 少了：{missing}；多了：{extra}。"
        f"若為預期內的契約變更，請同輪更新 test_wire_contract.py 的常數並確認前端消費端。"
    )
    # 型別只在「契約有指定 且 實際值非 None」時比對（nullable 欄夾具可能為空）
    bad = {
        k: (want, shape[k])
        for k, want in expected.items()
        if want is not None and shape[k] is not None and shape[k] != want
    }
    assert not bad, f"{label} 的 wire 欄型別已變動（期望 vs 實際）：{bad}"


# ── 夾具 ────────────────────────────────────────────────────────────────────


@pytest.fixture
def seeded(temp_db):
    """為每個契約端點各落一列最小夾具（欄位值不重要，形狀才重要）。"""
    db.insert_prejudge_run(
        {
            "job_id": "pj_wire0001",
            "kind": "batch",
            "rejudge": False,
            "source": "reviews",
            "model": "stub",
            "params": {"scope": "all"},
            "status": "running",
            "total": 1,
            "triggered_by": "wire@kkday.com",
        }
    )
    db.add_history_note("reviews", "R-wire-1", author="wire@kkday.com", content="契約夾具")
    db.create_batch(
        source="reviews",
        source_label="評論",
        original_name="wire.xlsx",
        row_count=1,
        note="契約夾具",
    )


# ── 契約測試 ────────────────────────────────────────────────────────────────


def test_prejudge_run_list_and_detail_wire(seeded):
    """`/api/v1/prejudge/runs` 與 `/runs/{job_id}`：run 欄位直出 + 詳情多一個 stages。"""
    items = db.list_prejudge_runs()["items"]
    assert items, "夾具未落庫"
    _assert_wire(items[0], _PREJUDGE_RUN_WIRE, "list_prejudge_runs.items[]")

    detail = db.prejudge_run_detail("pj_wire0001")
    assert detail is not None
    _assert_wire(detail, {**_PREJUDGE_RUN_WIRE, "stages": None}, "prejudge_run_detail")
    # log 必須被剔除：它是可觀的快照（實測既有資料平均約 70 KB/列），不該進列表/詳情回傳
    assert "log" not in detail


def test_attribution_history_wire(seeded):
    """`/api/attribution-history` 與 notes POST：兩者共用同一個序列化函式，形狀須一致。"""
    events = db.list_attribution_history("reviews", "R-wire-1")
    assert events
    _assert_wire(events[0], _ATTRIBUTION_HISTORY_WIRE, "list_attribution_history[]")

    created = db.add_history_note("reviews", "R-wire-2", author="w@kkday.com", content="x")
    _assert_wire(created, _ATTRIBUTION_HISTORY_WIRE, "add_history_note")


def test_batches_wire(seeded):
    """`/api/batches`：list 與 create 兩處各自硬編一份 key，形狀必須相同（現況是重複定義）。"""
    rows = db.list_batches()
    assert rows
    _assert_wire(rows[0], _BATCH_WIRE, "list_batches[]")

    created = db.create_batch(
        source="reviews",
        source_label="評論",
        original_name="wire2.xlsx",
        row_count=2,
    )
    _assert_wire(created, _BATCH_WIRE, "create_batch")


def test_attribution_dto_wire():
    """`attribution_dto` 是 DB 欄 → 前端 Attribution interface 的緩衝層，形狀即 wire 契約。

    純函式，不需要 DB。
    """
    dto = attribution_dto(
        {
            "polarity": "negative",
            "sentiment_score": 2,
            "prejudge_stage": "judged",
            "l1_code": "content",
            "l1_label": "商品內容",
            "l2_code": "C-1-1",
            "l2_label": "描述不符",
            "conf_value": 0.9,
            "conf_raw": 0.85,
            "conf_tier": "auto_accept",
            "summary": {"zh-tw": "摘要"},
            "evidence": "原文",
            "action": "建議",
            "model": "stub",
            "is_primary": True,
            "is_auto_accepted": False,
        }
    )
    _assert_wire(
        dto, _ATTRIBUTION_DTO_WIRE, "attribution_dto", structural=_ATTRIBUTION_DTO_STRUCTURAL
    )

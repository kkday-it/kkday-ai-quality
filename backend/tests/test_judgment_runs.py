"""歸因歷史（prejudge_runs）：run 生命週期落庫 + 列表/詳情聚合 + 重新初判判定（合成列，免 LLM）。"""

from app.core import db


def _run_row(job_id="pj_test01", kind="batch", **over):
    row = {
        "job_id": job_id,
        "kind": kind,
        "rejudge": False,
        "source": "product_reviews",
        "model": "gpt-5-mini",
        "ensemble_voters": 0,
        "params": {"scope": "all", "item_ids_count": 3},
        "status": "running",
        "total": 3,
        "triggered_by": "tester@kkday.com",
    }
    row.update(over)
    return row


def test_run_lifecycle_and_list(temp_db) -> None:
    """建檔 running → 狀態回寫 paused → 終態 finish 統計 → 列表含終態欄位。"""
    db.insert_prejudge_run(_run_row())
    db.update_prejudge_run_status("pj_test01", "paused")
    got = db.list_prejudge_runs()["items"][0]
    assert got["status"] == "paused" and got["kind"] == "batch" and got["finished_at"] is None

    db.finish_prejudge_run(
        "pj_test01",
        {
            "status": "done",
            "processed": 3,
            "ok": 2,
            "failed": 1,
            "total_tokens": 1234,
            "cost_usd": 0.05,
        },
    )
    data = db.list_prejudge_runs()
    assert data["total"] == 1
    got = data["items"][0]
    assert got["status"] == "done" and got["ok"] == 2 and got["failed"] == 1
    assert got["total_tokens"] == 1234 and got["finished_at"]  # ISO 字串
    assert got["params"]["item_ids_count"] == 3


def test_list_source_filter_and_paging(temp_db) -> None:
    """source 篩選 + limit/offset 分頁。"""
    db.insert_prejudge_run(_run_row(job_id="pj_a", source="product_reviews"))
    db.insert_prejudge_run(_run_row(job_id="pj_b", source="conversations", kind="single"))
    assert db.list_prejudge_runs(source="conversations")["total"] == 1
    assert db.list_prejudge_runs(limit=1)["total"] == 2
    assert len(db.list_prejudge_runs(limit=1)["items"]) == 1


def test_detail_aggregates_llm_usage_by_stage(temp_db) -> None:
    """詳情聚合同 job_id 的 llm_usage per-stage 明細（呼叫數/token/費用）。"""
    db.insert_prejudge_run(_run_row(job_id="pj_det"))
    db.insert_llm_usage_rows(
        [
            {
                "stage": "polarity",
                "model": "gpt-5-mini",
                "provider": "openai",
                "prompt_tokens": 100,
                "completion_tokens": 10,
                "reasoning_tokens": 0,
                "cached_tokens": 0,
                "total_tokens": 110,
                "cost_usd": 0.001,
                "source": "product_reviews",
                "source_id": "R1",
                "job_id": "pj_det",
            },
            {
                "stage": "attribute_b",
                "model": "gpt-5-mini",
                "provider": "openai",
                "prompt_tokens": 4000,
                "completion_tokens": 500,
                "reasoning_tokens": 250,
                "cached_tokens": 3000,
                "total_tokens": 4500,
                "cost_usd": 0.02,
                "source": "product_reviews",
                "source_id": "R1",
                "job_id": "pj_det",
            },
            # 別的 job 的呼叫不得混入
            {
                "stage": "polarity",
                "model": "gpt-5-mini",
                "provider": "openai",
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "reasoning_tokens": 0,
                "cached_tokens": 0,
                "total_tokens": 2,
                "cost_usd": 0.9,
                "source": "product_reviews",
                "source_id": "R2",
                "job_id": "pj_other",
            },
        ]
    )
    det = db.prejudge_run_detail("pj_det")
    assert det is not None and det["job_id"] == "pj_det"
    stages = {s["stage"]: s for s in det["stages"]}
    assert set(stages) == {"polarity", "attribute_b"}
    assert stages["attribute_b"]["calls"] == 1 and stages["attribute_b"]["reasoning_tokens"] == 250
    assert det["stages"][0]["stage"] == "attribute_b"  # 費用降冪排序

    assert db.prejudge_run_detail("pj_missing") is None


def test_any_judged_detects_rejudge(temp_db) -> None:
    """any_judged：標的已有初判→True（重新初判）；無初判/空清單/無來源→False。"""
    from app.core.schema import TicketFinding

    f = TicketFinding(
        finding_id="fd_product_reviews_R9",
        ticket_id="R9",
        recommended_action="no_action",
        polarity="negative",
        confidence=0.9,
        raw_confidence=0.9,
        confidence_tier="auto_accept",
        prejudge_stage="judged",
        model_used="stub",
    )
    db.replace_source_findings("product_reviews", "R9", [f])
    assert db.any_judged("product_reviews", ["R9", "R404"]) is True
    assert db.any_judged("product_reviews", ["R404"]) is False
    assert db.any_judged("conversations", ["R9"]) is False  # 跨來源不誤判
    assert db.any_judged("product_reviews", []) is False
    assert db.any_judged(None, ["R9"]) is False

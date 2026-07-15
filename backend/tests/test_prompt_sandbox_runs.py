"""Prompt 測試沙盒歷史（prompt_sandbox_runs）：落庫 + 列表分頁 + 詳情查詢。

與 prompt_eval_runs 完全分離的表（測試沙盒 vs 單支 prompt 指標評測），CRUD 形狀比照
test_prompt_eval_runs.py，額外驗證與正式 judgments/judgment_history 互不干擾（分離語意）。
"""

from __future__ import annotations

from app.core import db


def _row(**overrides) -> dict:
    base = {
        "source": "product_reviews",
        "scope": "single",
        "item_ids": ["r1"],
        "prompt_ids": ["polarity", "C-1"],
        "item_count": 1,
        "results": [{"source_id": "r1", "polarity": "negative", "prompts": []}],
        "log": [{"ts": 1.0, "kind": "stage", "stage": "job", "message": "測試"}],
        "model": "gpt-5-mini",
        "triggered_by": "qc@kkday.com",
        "job_id": "psbxjob_test",
    }
    base.update(overrides)
    return base


def test_insert_and_get_detail(temp_db) -> None:
    """落庫後 run_id 可查回完整詳情（含 results/log 完整快照）。"""
    run_id = db.insert_sandbox_run(_row())
    assert run_id.startswith("psbx_")
    detail = db.sandbox_run_detail(run_id)
    assert detail is not None
    assert detail["source"] == "product_reviews"
    assert detail["scope"] == "single"
    assert detail["item_ids"] == ["r1"]
    assert detail["prompt_ids"] == ["polarity", "C-1"]
    assert detail["results"][0]["polarity"] == "negative"
    assert detail["log"][0]["message"] == "測試"
    assert detail["triggered_by"] == "qc@kkday.com"
    assert detail["created_at"]  # server_default now()


def test_detail_unknown_run_id_returns_none(temp_db) -> None:
    assert db.sandbox_run_detail("psbx_不存在") is None


def test_list_sandbox_runs_ordered_and_excludes_heavy_cols(temp_db) -> None:
    """列表按 created_at 降冪；不含 results/log（體積可觀，詳情才展開）。"""
    db.insert_sandbox_run(_row(scope="single"))
    db.insert_sandbox_run(_row(scope="selection", item_ids=["r2", "r3"], item_count=2))

    out = db.list_sandbox_runs()
    assert out["total"] == 2
    assert "results" not in out["items"][0]
    assert "log" not in out["items"][0]
    assert {i["scope"] for i in out["items"]} == {"single", "selection"}


def test_list_sandbox_runs_pagination(temp_db) -> None:
    for i in range(3):
        db.insert_sandbox_run(_row(item_ids=[f"r{i}"]))
    page1 = db.list_sandbox_runs(limit=2, offset=0)
    page2 = db.list_sandbox_runs(limit=2, offset=2)
    assert page1["total"] == 3 and len(page1["items"]) == 2
    assert len(page2["items"]) == 1


def test_sandbox_run_isolated_from_judgments_tables(temp_db) -> None:
    """沙盒測試落庫後，judgments/judgment_history 完全無新列（測試歷史與正式初判分離）。"""
    from sqlalchemy import func, select

    from app.core.db import tables as T

    db.insert_sandbox_run(_row())
    with T.get_engine().connect() as c:
        j_count = c.execute(select(func.count()).select_from(T.judgments)).scalar()
        h_count = c.execute(select(func.count()).select_from(T.judgment_history)).scalar()
    assert j_count == 0
    assert h_count == 0

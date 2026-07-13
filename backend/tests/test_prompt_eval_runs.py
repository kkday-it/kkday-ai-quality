"""B2 Prompt 測試歷史（prompt_eval_runs）：落庫 + 列表分頁 + 詳情查詢。"""

from __future__ import annotations

from app.core import db


def _row(prompt_id: str = "C-3", **overrides) -> dict:
    base = {
        "prompt_id": prompt_id,
        "prompt_version": 5,
        "source": "production",
        "n": 10,
        "filters": None,
        "metrics": {"primary_match_rate": 0.8, "hit_rate": 0.9},
        "mismatches": [
            {"id": "R1", "ref": ["C-3-1"], "pack": [], "text": "評論", "reason": "棄權"}
        ],
        "model": "gpt-5-mini",
        "triggered_by": "qc@kkday.com",
    }
    base.update(overrides)
    return base


def test_insert_and_get_detail(temp_db) -> None:
    """落庫後 run_id 可查回完整詳情（含 filters/mismatches）。"""
    run_id = db.insert_prompt_eval_run(_row())
    assert run_id.startswith("peval_")
    detail = db.prompt_eval_run_detail(run_id)
    assert detail is not None
    assert detail["prompt_id"] == "C-3"
    assert detail["prompt_version"] == 5
    assert detail["source"] == "production"
    assert detail["metrics"]["primary_match_rate"] == 0.8
    assert detail["mismatches"][0]["id"] == "R1"
    assert detail["triggered_by"] == "qc@kkday.com"
    assert detail["created_at"]  # server_default now()


def test_detail_unknown_run_id_returns_none(temp_db) -> None:
    assert db.prompt_eval_run_detail("peval_不存在") is None


def test_list_prompt_eval_runs_scoped_and_ordered(temp_db) -> None:
    """列表僅回該 prompt_id 的紀錄，按 created_at 降冪；不含 mismatches（僅列指標摘要）。"""
    db.insert_prompt_eval_run(_row("C-3", n=10))
    db.insert_prompt_eval_run(_row("C-3", n=20))
    db.insert_prompt_eval_run(_row("C-1", n=5))  # 不同 prompt，不應出現在 C-3 列表

    out = db.list_prompt_eval_runs("C-3")
    assert out["total"] == 2
    assert all(item["prompt_id"] == "C-3" for item in out["items"])
    assert "mismatches" not in out["items"][0]

    out_c1 = db.list_prompt_eval_runs("C-1")
    assert out_c1["total"] == 1


def test_list_prompt_eval_runs_pagination(temp_db) -> None:
    for i in range(3):
        db.insert_prompt_eval_run(_row("C-6", n=i))
    page1 = db.list_prompt_eval_runs("C-6", limit=2, offset=0)
    page2 = db.list_prompt_eval_runs("C-6", limit=2, offset=2)
    assert page1["total"] == 3 and len(page1["items"]) == 2
    assert len(page2["items"]) == 1

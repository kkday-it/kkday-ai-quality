"""Prompt 測試沙盒 job runner（prompt_sandbox.py）：啟動 → 輪詢 → 結束落庫快照。

mock `prompt_eval._build_sandbox_item`/`sandbox_classify`（LLM 呼叫邊界，不需真 LLM key）+
`app_settings.resolve_provider_token`（guard 判定，明確給定而非依賴容器當下是否恰好無 key）。
"""

from __future__ import annotations

import time

import pytest

from app.core import settings as app_settings
from app.judge import prompt_eval
from app.judge import prompt_sandbox as ps


def _wait_done(job_id: str, timeout: float = 5.0) -> dict:
    """輪詢至 job 終態（done/error）或逾時。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        snap = ps.get_job(job_id)
        assert snap is not None
        if snap["status"] in ("done", "error"):
            return snap
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} 逾時未結束：{ps.get_job(job_id)}")


def test_guard_stub_rejects_unconditionally(monkeypatch):
    """無 token 的 eff → 無條件拒跑（非僅正式環境），比照 classify_one 既有慣例。

    明確 monkeypatch `resolve_provider_token` 回空（而非依賴容器當下是否恰好無 key）——
    guard 邏輯的正確性不該偶然依附於執行環境有沒有配置真實 OPENAI_API_KEY。
    """
    monkeypatch.setattr(app_settings, "resolve_provider_token", lambda eff: "")
    with pytest.raises(ValueError, match="stub"):
        ps.start("product_reviews", ["r1"], ["polarity"], {"model": ""}, scope="single")


def test_start_run_and_persist_snapshot(temp_db, monkeypatch):
    """啟動 → 輪詢至 done → prompt_sandbox_runs 有一筆，results 逐筆齊、log 快照非空。"""
    monkeypatch.setattr(app_settings, "resolve_provider_token", lambda eff: "sk-fake")  # 過 guard
    monkeypatch.setattr(
        prompt_eval, "_build_sandbox_item", lambda source, sid: {"source_id": sid, "raw": {}}
    )

    def _fake_classify(item, prompt_ids, model):
        return {
            "source_id": item["source_id"],
            "text": "測試文字",
            "polarity": "negative",
            "sentiment_score": 2,
            "prompts": [{"prompt_id": "polarity", "matched": True, "reason": "假測試"}],
        }

    monkeypatch.setattr(prompt_eval, "sandbox_classify", _fake_classify)

    eff = {"token": "sk-fake", "model": "gpt-5-mini", "base_url": ""}
    job_id = ps.start(
        "product_reviews",
        ["r1", "r2"],
        ["polarity"],
        eff,
        scope="selection",
        triggered_by="qc@kkday.com",
    )
    assert job_id.startswith("psbxjob_")

    snap = _wait_done(job_id)
    assert snap["status"] == "done"
    assert snap["total"] == 2
    assert snap["done"] == 2
    assert snap["run_id"] is not None

    from app.core import db

    detail = db.sandbox_run_detail(snap["run_id"])
    assert detail is not None
    assert detail["scope"] == "selection"
    assert detail["item_count"] == 2
    assert {r["source_id"] for r in detail["results"]} == {"r1", "r2"}
    # log 快照非空——job 啟動時 run_log.emit("stage","job",...) 至少一條，回看需求坐實
    assert len(detail["log"]) >= 1
    assert any(e["stage"] == "job" for e in detail["log"])


def test_run_records_per_item_error_without_failing_whole_job(temp_db, monkeypatch):
    """單筆判決失敗（如找不到評論）不擋全批：該筆記錯誤，job 仍 done，其餘筆正常落庫。"""
    monkeypatch.setattr(app_settings, "resolve_provider_token", lambda eff: "sk-fake")  # 過 guard
    monkeypatch.setattr(
        prompt_eval, "_build_sandbox_item", lambda source, sid: {"source_id": sid, "raw": {}}
    )

    def _fake_classify(item, prompt_ids, model):
        if item["source_id"] == "bad":
            raise ValueError("找不到評論：product_reviews/bad")
        return {"source_id": item["source_id"], "prompts": []}

    monkeypatch.setattr(prompt_eval, "sandbox_classify", _fake_classify)

    eff = {"token": "sk-fake", "model": "gpt-5-mini", "base_url": ""}
    job_id = ps.start("product_reviews", ["ok", "bad"], ["polarity"], eff, scope="selection")
    snap = _wait_done(job_id)

    assert snap["status"] == "done"
    from app.core import db

    detail = db.sandbox_run_detail(snap["run_id"])
    by_id = {r["source_id"]: r for r in detail["results"]}
    assert "prompts" in by_id["ok"]
    assert "error" in by_id["bad"]

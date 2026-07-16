"""Prompt 測試沙盒 job runner（prompt_sandbox.py）：啟動 → 輪詢 → 結束落庫快照。

mock `prompt_eval._build_sandbox_item`/`sandbox_classify`（LLM 呼叫邊界，不需真 LLM key）+
`app_settings.resolve_provider_token`（guard 判定，明確給定而非依賴容器當下是否恰好無 key）。
"""

from __future__ import annotations

import time

import pytest

from app.core import db as core_db
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
    """無 token 的 eff → 無條件拒跑（非僅正式環境）。

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

    def _fake_classify(item, prompt_ids, model, *, versions=None):
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

    def _fake_classify(item, prompt_ids, model, *, versions=None):
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


# ─────────────────────────── 版本選擇功能：versions fail-fast ───────────────────────────
def test_start_versions_unknown_rule_code_fails_fast(monkeypatch):
    """versions 帶未知 rule_code → fail-fast，不派工。"""
    monkeypatch.setattr(app_settings, "resolve_provider_token", lambda eff: "sk-fake")
    with pytest.raises(ValueError, match="未知 rule_code"):
        ps.start(
            "product_reviews",
            ["r1"],
            ["polarity"],
            {"model": "gpt-5-mini"},
            scope="single",
            versions={"prompt_not_a_rule": 1},
        )


def test_start_versions_nonexistent_version_fails_fast(temp_db, monkeypatch):
    """versions 指定不存在的版本號 → fail-fast，不派工。"""
    monkeypatch.setattr(app_settings, "resolve_provider_token", lambda eff: "sk-fake")
    with pytest.raises(ValueError, match="無版本"):
        ps.start(
            "product_reviews",
            ["r1"],
            ["polarity"],
            {"model": "gpt-5-mini"},
            scope="single",
            versions={"prompt_polarity": 9999},
        )


def test_start_versions_thread_through_to_sandbox_classify_and_persist(temp_db, monkeypatch):
    """versions 一路貫穿到 sandbox_classify，且落庫的 versions 欄與請求一致。"""
    monkeypatch.setattr(app_settings, "resolve_provider_token", lambda eff: "sk-fake")
    monkeypatch.setattr(
        prompt_eval, "_build_sandbox_item", lambda source, sid: {"source_id": sid, "raw": {}}
    )
    monkeypatch.setattr(
        core_db, "get_rule_version", lambda code, version: {"text": "# x\n## System\n```\nx\n```"}
    )

    seen: list[dict | None] = []

    def _fake_classify(item, prompt_ids, model, *, versions=None):
        seen.append(versions)
        return {"source_id": item["source_id"], "prompts": []}

    monkeypatch.setattr(prompt_eval, "sandbox_classify", _fake_classify)

    eff = {"token": "sk-fake", "model": "gpt-5-mini", "base_url": ""}
    job_id = ps.start(
        "product_reviews",
        ["r1"],
        ["polarity"],
        eff,
        scope="single",
        versions={"prompt_polarity": 2},
    )
    snap = _wait_done(job_id)
    assert snap["status"] == "done"
    assert seen == [{"prompt_polarity": 2}]

    detail = core_db.sandbox_run_detail(snap["run_id"])
    assert detail["versions"] == {"prompt_polarity": 2}


def test_no_versions_persists_empty_dict(temp_db, monkeypatch):
    """無 versions → 落庫的 versions 欄為空 dict（server_default），沙盒行為與 v1 一致。"""
    monkeypatch.setattr(app_settings, "resolve_provider_token", lambda eff: "sk-fake")
    monkeypatch.setattr(
        prompt_eval, "_build_sandbox_item", lambda source, sid: {"source_id": sid, "raw": {}}
    )
    monkeypatch.setattr(
        prompt_eval,
        "sandbox_classify",
        lambda item, prompt_ids, model, *, versions=None: {
            "source_id": item["source_id"],
            "prompts": [],
        },
    )
    eff = {"token": "sk-fake", "model": "gpt-5-mini", "base_url": ""}
    job_id = ps.start("product_reviews", ["r1"], ["polarity"], eff, scope="single")
    snap = _wait_done(job_id)
    detail = core_db.sandbox_run_detail(snap["run_id"])
    assert detail["versions"] == {}

"""prejudge_batch（批量編排：in-mem job registry + ThreadPool）行為測試——此前零覆蓋。

不打真 LLM、不碰真 DB：monkeypatch `prejudge.to_findings` 與 `db.get_items_by_ids` /
`db.replace_source_findings` / `db.insert_llm_usage_rows`。覆蓋四塊：
- 整批跑完：start_job → running → done，processed/ok 對齊。
- 狀態機：pause/resume/cancel 的合法轉移與非法拒絕（含 gate/cancel Event 副作用）。
- _bump 併發累計 thread-safe。
- copy_context 快照：worker 內能讀到 _run 注入的 effective 設定（contextvar 跨線程傳遞，
  全模組最脆弱處——快照時機錯了 worker 會拿到 stub 空設定）。
"""

from __future__ import annotations

import threading
import time

import pytest

from app.core import db
from app.core import settings as app_settings
from app.judge import prejudge
from app.judge import prejudge_batch as pb


def _wait_status(job_id: str, want: set[str], timeout: float = 5.0) -> dict:
    """輪詢 job 至狀態進入 want 集合（測試安全網：逾時 fail 而非卡死）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snap = pb.get_job(job_id)
        if snap and snap["status"] in want:
            return snap
        time.sleep(0.02)
    pytest.fail(f"job {job_id} 未在 {timeout}s 內達到 {want}（現況 {pb.get_job(job_id)}）")


@pytest.fixture
def batch_env(monkeypatch):
    """隔離批次環境：初判/落庫/撈件全 stub 化（僅測編排層，不測初判本體）。

    回傳 mutable dict 供個別測試覆寫 to_findings 行為與讀取落庫紀錄。
    """
    state: dict = {"replaced": [], "usage_flushed": []}

    monkeypatch.setattr(pb, "_reload_judge_rules", lambda: None)  # 免碰判準 loader/DB
    monkeypatch.setattr(
        db,
        "get_items_by_ids",
        lambda ids, source=None: [
            {"rec_oid": i, "comment": "很差要退款", "rating": 1} for i in ids
        ],
    )
    monkeypatch.setattr(
        db,
        "replace_source_findings",
        # **kw 吸收評論級歷史 kwargs（params/job_id/triggered_by），mock 不落史
        lambda src, sid, findings, **kw: state["replaced"].append((src, sid)) or len(findings),
    )
    monkeypatch.setattr(
        db, "insert_llm_usage_rows", lambda rows: state["usage_flushed"].append(len(rows)) or 0
    )
    # 歸因歷史（prejudge_runs）落庫也 stub 掉：start_job/暫停恢復/終態都會 best-effort 寫 DB，
    # 不 stub 會把測試 job 寫進 dev 庫（實測汙染過 4 列假 run）。
    monkeypatch.setattr(
        db, "insert_prejudge_run", lambda row: state.setdefault("runs", []).append(row)
    )
    monkeypatch.setattr(db, "update_prejudge_run_status", lambda job_id, status: None)
    monkeypatch.setattr(db, "finish_prejudge_run", lambda job_id, snap: None)
    monkeypatch.setattr(prejudge, "to_findings", lambda item, **kw: [])
    return state


_EFF = {"provider": "openai", "base_url": "", "model": "gpt-5-mini", "api_token": ""}


def test_start_job_runs_to_done(batch_env):
    """整批跑完：三筆全 ok、status=done、落庫每筆一次（(source, source_id) 對齊）。"""
    job_id = pb.start_job(["r1", "r2", "r3"], dict(_EFF), "gpt-5-mini", source="product_reviews")
    snap = _wait_status(job_id, {"done"})
    assert snap["total"] == 3 and snap["processed"] == 3
    assert snap["ok"] == 3 and snap["failed"] == 0
    assert sorted(sid for _, sid in batch_env["replaced"]) == ["r1", "r2", "r3"]
    assert all(src == "product_reviews" for src, _ in batch_env["replaced"])


def test_resolve_versions_used_merges_pinned_over_active(monkeypatch):
    """_resolve_versions_used：沒指定的補 active 版本號，指定的（pinned）覆蓋 active。"""
    monkeypatch.setattr(
        db,
        "list_rule_meta",
        lambda: [
            {"rule_code": "prompt_polarity", "version": 5},
            {"rule_code": "prompt_C-1", "version": 3},
            {"rule_code": "not_a_prompt", "version": 99},  # 非 prompt_* rule_code，應被過濾
        ],
    )
    out = pb._resolve_versions_used({"prompt_C-1": 1})  # 指定 C-1 用舊版 1（覆蓋 active 的 3）
    assert out == {"prompt_polarity": 5, "prompt_C-1": 1}


def test_resolve_versions_used_no_pinned_returns_active_snapshot(monkeypatch):
    """完全沒指定 → 回純 active 快照（供稽核，即使使用者這次沒選任何版本）。"""
    monkeypatch.setattr(
        db, "list_rule_meta", lambda: [{"rule_code": "prompt_polarity", "version": 7}]
    )
    assert pb._resolve_versions_used(None) == {"prompt_polarity": 7}


def test_start_job_writes_prompt_versions_into_history_params(batch_env, monkeypatch):
    """指定 prompt_versions 啟動 → history_params（傳給 replace_source_findings 的 params）含
    完整版本快照（稽核軌跡落地）。"""
    monkeypatch.setattr(
        db, "list_rule_meta", lambda: [{"rule_code": "prompt_polarity", "version": 5}]
    )
    captured: list[dict] = []
    monkeypatch.setattr(
        db,
        "replace_source_findings",
        lambda src, sid, findings, **kw: captured.append(kw.get("params")) or len(findings),
    )
    job_id = pb.start_job(
        ["r1"],
        dict(_EFF),
        "gpt-5-mini",
        source="product_reviews",
        prompt_versions={"prompt_C-1": 2},
    )
    _wait_status(job_id, {"done"})
    assert len(captured) == 1
    assert captured[0]["prompt_versions"] == {"prompt_polarity": 5, "prompt_C-1": 2}


def test_single_item_failure_isolated(batch_env, monkeypatch):
    """單筆初判炸掉只計 failed，不中斷整批（其餘筆照常 ok）。"""

    def boom(item, **kw):
        if item.get("rec_oid") == "bad":
            raise RuntimeError("模擬單筆失敗")
        return []

    monkeypatch.setattr(prejudge, "to_findings", boom)
    job_id = pb.start_job(["a", "bad", "b"], dict(_EFF), "m", source="product_reviews")
    snap = _wait_status(job_id, {"done"})
    assert snap["ok"] == 2 and snap["failed"] == 1 and snap["processed"] == 3


def test_pause_resume_cancel_state_machine():
    """狀態機單元：合法轉移改狀態 + 撥動 Event；非法轉移回 False 不動狀態。

    直接構造 registry 條目（不跑真 thread），確定性驗證 pause/resume/cancel 的
    前置條件（running→paused→running；終態拒絕）與 gate/cancel Event 副作用。
    """
    job_id = "pj_test_sm"
    gate, cancel = threading.Event(), threading.Event()
    gate.set()
    pb._store.put(job_id, pb._new_snapshot(1, "m"))
    with pb._controls_lock:
        pb._controls[job_id] = {"gate": gate, "cancel": cancel}
    try:
        assert pb.resume_job(job_id) is False  # running 不可 resume
        assert pb.pause_job(job_id) is True and pb.get_job(job_id)["status"] == "paused"
        assert not gate.is_set()  # 暫停清 gate（提交迴圈阻塞）
        assert pb.pause_job(job_id) is False  # paused 不可再 pause
        assert pb.resume_job(job_id) is True and pb.get_job(job_id)["status"] == "running"
        assert gate.is_set()
        assert pb.cancel_job(job_id) is True and pb.get_job(job_id)["status"] == "cancelling"
        assert cancel.is_set() and gate.is_set()  # cancel 同時喚醒暫停迴圈
        pb._set_status(job_id, "cancelled")
        assert pb.cancel_job(job_id) is False  # 終態拒絕
        assert pb.pause_job(job_id) is False
        assert pb.get_job("nonexistent") is None  # 不存在回 None（端點轉 404）
    finally:
        pb._store.delete(job_id)
        with pb._controls_lock:
            pb._controls.pop(job_id, None)


def test_cancel_running_job_drains_to_cancelled(batch_env, monkeypatch):
    """取消跑批：首筆初判阻塞時 cancel → drain 已提交筆 → 終態 cancelled（非 done）。"""
    release = threading.Event()
    entered = threading.Event()

    def blocking(item, **kw):
        entered.set()
        release.wait(5)  # 佔住 worker，讓 cancel 先於整批完成發生
        return []

    monkeypatch.setattr(prejudge, "to_findings", blocking)
    job_id = pb.start_job([f"r{i}" for i in range(6)], dict(_EFF), "m", source="product_reviews")
    assert entered.wait(5)  # 確認至少一筆已在跑
    assert pb.cancel_job(job_id) is True
    release.set()  # 放行 in-flight，讓 drain 收斂
    snap = _wait_status(job_id, {"cancelled"})
    assert snap["status"] == "cancelled"


def test_bump_thread_safe_counts():
    """_bump 併發累計：N 線程各 bump 一次，processed/ok/failed 無競態遺失。"""
    job_id = "pj_test_bump"
    pb._store.put(job_id, pb._new_snapshot(40, "m"))
    try:
        threads = [
            threading.Thread(target=pb._bump, args=(job_id,), kwargs={"ok": i % 4 != 0})
            for i in range(40)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        snap = pb.get_job(job_id)
        assert snap["processed"] == 40
        assert snap["ok"] == 30 and snap["failed"] == 10
        assert pb._bump("nonexistent", ok=True) is None  # 不存在 job 靜默忽略
    finally:
        pb._store.delete(job_id)


def test_copy_context_carries_settings_into_worker(batch_env, monkeypatch):
    """copy_context 快照：worker 內 settings.current() 須等於 _run 注入的 effective dict。

    這是本模組最脆弱的機制——快照若在 set_current 之前產生（或 worker 未經 ctx.run），
    worker 會拿到 stub 空設定、真判默默變 stub 判。鎖住 model 與 api_token（per-config）傳遞。
    """
    seen: list[dict] = []

    def capture(item, **kw):
        seen.append(dict(app_settings.current()))
        return []

    monkeypatch.setattr(prejudge, "to_findings", capture)
    eff = {**_EFF, "model": "gpt-5.4", "api_token": "sk-ctx-test"}
    job_id = pb.start_job(["r1", "r2"], eff, "gpt-5.4", source="product_reviews")
    _wait_status(job_id, {"done"})
    assert len(seen) == 2
    for cur in seen:
        assert cur.get("model") == "gpt-5.4"
        assert cur.get("api_token") == "sk-ctx-test"


def test_run_second_gate_blocks_stub_in_production(batch_env, monkeypatch):
    """正式環境 stub 第二道防線：解不出任何 token 的批次直接標 error、零筆處理。

    主閘在 judgment router；此閘防繞過 API 直呼 start_job 的路徑（腳本/排程誤用）。
    """
    from app.core import config

    monkeypatch.setattr(pb, "is_production", lambda: True)
    monkeypatch.setattr(config.env, "openai_api_key", "")  # 斷開 env fallback，確保解不出 token
    job_id = pb.start_job(["r1", "r2"], dict(_EFF), "gpt-5-mini", source="product_reviews")
    snap = _wait_status(job_id, {"error"})
    assert snap["processed"] == 0
    assert batch_env["replaced"] == []  # 零筆落庫


def test_run_second_gate_passes_with_token_in_production(batch_env, monkeypatch):
    """正式環境有真 token（該配置自身 api_token）→ 防線放行、整批照跑。"""
    monkeypatch.setattr(pb, "is_production", lambda: True)
    eff = {**_EFF, "api_token": "sk-real"}
    job_id = pb.start_job(["r1", "r2"], eff, "gpt-5-mini", source="product_reviews")
    snap = _wait_status(job_id, {"done"})
    assert snap["processed"] == 2

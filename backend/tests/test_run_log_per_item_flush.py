"""run_log 逐筆緩衝：take 取走即清、大批量（live=False）照樣收、job 級事件獨立成桶。

守的是「所有 job 都有日誌」這條線——舊設計以 job 筆數當閘門（>20 筆完全不收），
批量初判因此查不到任何 LLM 執行日誌。現在閘門只管 SSE 即時佇列，落庫緩衝一律收。
"""

from __future__ import annotations

from contextvars import copy_context

from app.judge import run_log


def test_take_returns_and_clears_per_item():
    """take 取走該評論的條目後桶即清空（記憶體不隨批量累積）。"""
    run_log.bind("job_flush_1")
    run_log.bind_item("rev_A")
    run_log.emit("stage", "item", "開始初判 rev_A")
    run_log.emit("llm_response", "polarity", "回應", label="polarity")

    first = run_log.take("job_flush_1", "rev_A")
    assert [e["message"] for e in first] == ["開始初判 rev_A", "回應"]
    assert run_log.take("job_flush_1", "rev_A") == []  # 已清空，不會重複落庫


def test_large_job_still_buffers_per_item():
    """live=False（大批量不建 SSE 佇列）時，逐筆緩衝照收——這正是拆表要解的缺口。"""
    run_log.bind("job_flush_2", live=False)
    run_log.bind_item("rev_B")
    run_log.emit("stage", "item", "開始初判 rev_B")

    _entries, _done, exists = run_log.read("job_flush_2")
    assert not exists, "大批量不應建立 SSE 即時佇列"
    assert [e["message"] for e in run_log.take("job_flush_2", "rev_B")] == ["開始初判 rev_B"]


def test_job_level_events_go_to_their_own_bucket():
    """未綁 item 的 job 級事件獨立成桶（落庫時 source_id=''），不混進任何評論。"""
    run_log.bind("job_flush_3")
    run_log.bind_item(None)  # 測試隔離：contextvar 會跨 test function 殘留（同一條 thread）
    run_log.emit("stage", "job", "初判任務啟動：2 筆")  # job 級：未綁任何評論

    def _one(iid: str) -> None:
        run_log.bind_item(iid)
        run_log.emit("stage", "item", f"開始初判 {iid}")

    for iid in ("rev_C", "rev_D"):
        copy_context().run(_one, iid)  # 對齊 ThreadPool 派工：每筆獨立 context 快照

    assert [e["message"] for e in run_log.take("job_flush_3", None)] == ["初判任務啟動：2 筆"]
    assert [e["message"] for e in run_log.take("job_flush_3", "rev_C")] == ["開始初判 rev_C"]
    assert [e["message"] for e in run_log.take("job_flush_3", "rev_D")] == ["開始初判 rev_D"]


def test_drop_job_clears_leftovers():
    """drop_job 兜底清掉殘留桶（取消時已提交但未走完 _work_one 的筆）。"""
    run_log.bind("job_flush_4")
    run_log.bind_item("rev_E")
    run_log.emit("stage", "item", "開始初判 rev_E")

    run_log.drop_job("job_flush_4")
    assert run_log.take("job_flush_4", "rev_E") == []

"""run_log item 蓋章：bind_item 後 emit 自動帶 item_id、未 bind 不帶、copy_context 隔離繼承。"""

from contextvars import copy_context

from app.judge import run_log


def test_emit_stamps_item_id_when_bound():
    run_log.bind("job_stamp_1")
    run_log.bind_item("rev_A")
    try:
        run_log.emit("stage", "item", "開始初判 rev_A")
        entries, _, _ = run_log.read("job_stamp_1", 0)
        assert entries[-1]["item_id"] == "rev_A"
    finally:
        run_log.bind_item(None)


def test_emit_without_bind_item_has_no_field():
    run_log.bind("job_stamp_2")
    run_log.emit("stage", "job", "初判任務啟動")
    entries, _, _ = run_log.read("job_stamp_2", 0)
    assert "item_id" not in entries[-1]


def test_copy_context_inherits_and_isolates_item():
    """派工模型：worker 於 copied context 內 bind_item → 互不污染、父 context 不受影響。"""
    run_log.bind("job_stamp_3")

    def _worker(iid: str) -> None:
        run_log.bind_item(iid)
        run_log.emit("stage", "item", f"開始初判 {iid}")

    for iid in ("rev_X", "rev_Y"):
        copy_context().run(_worker, iid)
    run_log.emit("stage", "job", "job 級收尾")  # 父 context 未 bind_item
    entries, _, _ = run_log.read("job_stamp_3", 0)
    assert [e.get("item_id") for e in entries[-3:]] == ["rev_X", "rev_Y", None]

"""導出 job 被行程重啟打斷時的可觀測性回歸。

背景（實際事故）：dev 的 uvicorn --reload 只要有人存了 backend 任一 .py 就重啟，跑到一半的
導出 job 隨舊行程消失。舊行為下 `mark_running_interrupted()` 雖然標了 interrupted，但快照本身
也在記憶體、跟著沒了 → 下個行程的 download 只能回「job 不存在」的 404，使用者無從得知原因。

本檔鎖住修復後的契約：標記 → 落盤 → 新行程 restore 得回來 → 端點答得出「被重啟打斷」。
"""

from __future__ import annotations

from app.core import export_jobs
from app.core.job_registry import JobStore


def test_persist_restore_round_trip(tmp_path) -> None:
    """persist 只寫指定狀態；restore 讀回後即刪檔（避免下次重啟誤當本次殘留）。"""
    store: JobStore = JobStore(persist_dir=tmp_path)
    store.put("j1", {"status": "interrupted", "filename": "a.xlsx"})
    store.put("j2", {"status": "done", "filename": "b.xlsx"})
    assert store.persist(statuses=("interrupted",)) == 1  # done 不落盤

    fresh: JobStore = JobStore(persist_dir=tmp_path)  # 模擬新行程
    assert fresh.restore() == 1
    assert fresh.get("j1")["status"] == "interrupted"
    assert fresh.get("j2") is None
    assert not (tmp_path / "jobs.json").exists()  # 讀完即刪
    assert JobStore(persist_dir=tmp_path).restore() == 0  # 再啟動不會重複讀到


def test_restore_does_not_clobber_live_jobs(tmp_path) -> None:
    """restore 只補不存在的 id：真跑起來的同 id job 狀態不得被舊快照蓋回去。"""
    store: JobStore = JobStore(persist_dir=tmp_path)
    store.put("j1", {"status": "interrupted"})
    store.persist()

    fresh: JobStore = JobStore(persist_dir=tmp_path)
    fresh.put("j1", {"status": "running"})  # 新行程有個活的同 id
    assert fresh.restore() == 0
    assert fresh.get("j1")["status"] == "running"


def test_restore_survives_missing_and_corrupt_file(tmp_path) -> None:
    """無檔案（乾淨啟動）與壞檔都不得擋住開機——留痕是加分項，不是啟動的前提。"""
    assert JobStore(persist_dir=tmp_path).restore() == 0
    (tmp_path / "jobs.json").write_text("{壞掉的 json", encoding="utf-8")
    assert JobStore(persist_dir=tmp_path).restore() == 0
    assert JobStore().restore() == 0  # 未設 persist_dir＝純記憶體，no-op


def test_mark_running_interrupted_persists(tmp_path, monkeypatch) -> None:
    """running/cancelling 皆轉 interrupted 並落盤；終態 job 不受影響。"""
    store: JobStore = JobStore(persist_dir=tmp_path)
    monkeypatch.setattr(export_jobs, "_store", store)
    store.put("run", {"status": "running"})
    store.put("cancelling", {"status": "cancelling"})  # 停止指令送出後也可能撞上重啟
    store.put("fin", {"status": "done"})

    assert sorted(export_jobs.mark_running_interrupted()) == ["cancelling", "run"]
    assert store.get("fin")["status"] == "done"

    fresh: JobStore = JobStore(persist_dir=tmp_path)
    assert fresh.restore() == 2
    assert fresh.get("run")["status"] == "interrupted"


def test_interrupted_is_terminal() -> None:
    """interrupted 必須是終態：否則 SSE 不會關流，前端會一直轉（本次事故的表徵之一）。"""
    assert export_jobs.is_terminal("interrupted")
    for s in ("done", "error", "cancelled"):
        assert export_jobs.is_terminal(s)
    assert not export_jobs.is_terminal("running")

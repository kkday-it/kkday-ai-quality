"""graceful shutdown 收尾（shutdown.mark_running_jobs_interrupted）測試。

鎖兩件事：①lifespan shutdown 觸發時，各 registry 進行中 job 被標 interrupted
②終態 job（done/error/cancelled）不被誤標。
"""

from __future__ import annotations

from app.core import export_jobs, import_jobs
from app.core.shutdown import mark_running_jobs_interrupted
from app.judge import prejudge_batch, prompt_sandbox
from app.judge.ingest import upload_batch


def test_marks_running_and_paused_but_not_terminal(monkeypatch) -> None:
    """running（各 registry）與 paused（prejudge）被標 interrupted；終態不動。"""
    monkeypatch.setattr(
        export_jobs._store, "_jobs", {"e1": {"status": "running"}, "e2": {"status": "done"}}
    )
    monkeypatch.setattr(import_jobs._store, "_jobs", {"i1": {"status": "error"}})
    monkeypatch.setattr(
        prejudge_batch._store,
        "_jobs",
        {"p1": {"status": "paused"}, "p2": {"status": "cancelled"}, "p3": {"status": "running"}},
    )
    monkeypatch.setattr(upload_batch._store, "_jobs", {"u1": {"status": "running"}})
    monkeypatch.setattr(prompt_sandbox._store, "_jobs", {"s1": {"status": "running"}})

    hit = mark_running_jobs_interrupted()

    assert hit == {
        "export": ["e1"],
        "prejudge": ["p1", "p3"],
        "upload": ["u1"],
        "prompt_sandbox": ["s1"],
    }
    assert export_jobs._store._jobs["e1"]["status"] == "interrupted"
    assert export_jobs._store._jobs["e2"]["status"] == "done"  # 終態不動
    assert import_jobs._store._jobs["i1"]["status"] == "error"
    assert prejudge_batch._store._jobs["p1"]["status"] == "interrupted"  # paused 也標
    assert prejudge_batch._store._jobs["p2"]["status"] == "cancelled"
    assert upload_batch._store._jobs["u1"]["status"] == "interrupted"
    assert prompt_sandbox._store._jobs["s1"]["status"] == "interrupted"  # 2026-07-23 補：先前遺漏


def test_lifespan_shutdown_invokes_marking(temp_db, monkeypatch) -> None:
    """TestClient with-block 退出（lifespan shutdown）→ registry 內 running job 轉 interrupted。"""
    from fastapi.testclient import TestClient

    from app.api.main import app

    monkeypatch.setattr(export_jobs._store, "_jobs", {"live": {"status": "running"}})
    with TestClient(app) as client:
        assert client.get("/api/status").status_code == 200
    assert export_jobs._store._jobs["live"]["status"] == "interrupted"

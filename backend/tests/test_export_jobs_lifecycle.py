"""export_jobs 生命週期測試（原零覆蓋）。

不經 HTTP：直接驅動 core.export_jobs 的 job registry（背景 thread 真實跑，輪詢等終態）。
"""

from __future__ import annotations

import time

import pytest

from app.api.routers.exports import _mime_for
from app.core import export_jobs


def _wait_terminal(job_id: str, timeout: float = 5.0) -> dict:
    """輪詢至終態（done/error/cancelled）；逾時視為測試失敗。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        snap = export_jobs.get_job(job_id)
        if snap and snap["status"] in ("done", "error", "cancelled"):
            return snap
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} 未在 {timeout}s 內達終態")


def test_local_roundtrip_default_destination() -> None:
    """成功導出：進度收斂、bytes 可 pop。"""

    def _builder(ctx: export_jobs.ExportCtx) -> bytes:
        ctx.report(1, 2)
        ctx.report(2, 2)
        return b"file-bytes"

    job_id = export_jobs.start_export(_builder, "a.xlsx")
    snap = _wait_terminal(job_id)
    assert snap["status"] == "done"
    assert snap["processed"] == snap["total"] == 2
    assert export_jobs.pop_result(job_id) == b"file-bytes"
    assert export_jobs.get_job(job_id) is None  # pop 後連快照一併清
    assert export_jobs.pop_result(job_id) is None  # 一次性


def test_cancel_converges_without_result() -> None:
    """cancel：builder 於下個 check 點收斂為 cancelled，不產出檔案。"""

    def _builder(ctx: export_jobs.ExportCtx) -> bytes:
        for i in range(500):
            ctx.check()
            ctx.report(i, 500)
            time.sleep(0.01)
        return b"never"

    job_id = export_jobs.start_export(_builder, "a.xlsx")
    assert export_jobs.cancel_export(job_id) is True
    snap = _wait_terminal(job_id)
    assert snap["status"] == "cancelled"
    assert export_jobs.pop_result(job_id) is None


def test_builder_error_marks_error() -> None:
    """builder 例外：job 標 error 並透出訊息（不靜默）。"""

    def _builder(ctx: export_jobs.ExportCtx) -> bytes:
        raise ValueError("組檔爆炸")

    job_id = export_jobs.start_export(_builder, "a.xlsx")
    snap = _wait_terminal(job_id)
    assert snap["status"] == "error" and "組檔爆炸" in snap["error"]


def test_cancel_rejected_on_terminal_job() -> None:
    """終態 job 不可再 cancel（回 False，狀態不動）。"""
    job_id = export_jobs.start_export(lambda ctx: b"x", "a.xlsx")
    snap = _wait_terminal(job_id)
    assert snap["status"] == "done"
    assert export_jobs.cancel_export(job_id) is False


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("a.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        ("A.ZIP", "application/zip"),
        ("a.csv", "text/csv"),
        ("unknown.bin", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ],
)
def test_download_mime_by_extension(name: str, expected: str) -> None:
    """download MIME 依副檔名判定（未知副檔名回 xlsx 歷史預設）。"""
    assert _mime_for(name) == expected

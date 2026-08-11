"""job 進度 SSE 的終態判定回歸鎖。

2026-08-11 實測缺陷：四個 router 各自手寫 SSE 迴圈，其中三份的終態判定是不完整的子集——
inbound / admin_import 只認 `("done", "error")`、v1.prejudge 只認多一個 `cancelled`。
而 `shutdown.mark_running_jobs_interrupted()` 會把進行中的 job 設成 `interrupted`
（import_jobs 與 upload_batch 都有 `mark_running_interrupted()`），於是那三條串流
**永遠不會結束**：前端對一個再也不會變的 job 無限輪詢，且不會拋任何錯。

修法是把終態收斂到 `job_registry.is_terminal()` 並讓四個 router 共用 `_sse.job_progress_stream`。
本檔鎖住兩件事：SSOT 的內容，以及「沒有人再手刻終態清單」。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.core import job_registry

_ROUTERS = Path(__file__).resolve().parents[1] / "app" / "api" / "routers"


def test_terminal_statuses_cover_interrupted() -> None:
    """`interrupted` 必須算終態——它是 graceful shutdown 的產物，漏了就串流不止。"""
    assert job_registry.is_terminal("interrupted")
    assert set(job_registry.TERMINAL_STATUSES) == {"done", "error", "cancelled", "interrupted"}
    assert not job_registry.is_terminal("running")


@pytest.mark.parametrize("rel", ["exports.py", "inbound.py", "admin_import.py", "v1/prejudge.py"])
def test_routers_do_not_hardcode_terminal_statuses(rel: str) -> None:
    """router 內不得再出現手刻的終態字串清單——只能走 job_registry 的 SSOT。

    以原始碼比對而非行為比對是刻意的：行為測試要真的跑起一個 job 才驗得到，
    而這個缺陷的本質是「有人複製貼上時漏了一個狀態」，那在原始碼層就看得出來。
    """
    src = (_ROUTERS / rel).read_text(encoding="utf-8")
    bad = re.findall(r'\(\s*"done"\s*,\s*"error"[^)]*\)', src)
    assert not bad, f"{rel} 仍手刻終態清單 {bad}——請改用 job_registry.is_terminal()"


def test_all_job_progress_endpoints_share_one_generator() -> None:
    """四個 job 進度端點都必須走共用產生器（各自手寫就是缺陷的來源）。"""
    for rel in ("exports.py", "inbound.py", "admin_import.py", "v1/prejudge.py"):
        src = (_ROUTERS / rel).read_text(encoding="utf-8")
        assert "job_progress_stream" in src, f"{rel} 未使用共用 SSE 產生器"


def test_sse_headers_are_single_sourced() -> None:
    """SSE header 只有一份定義——語義不同而無法共用迴圈的端點仍共用 header。"""
    hits = [
        p.name
        for p in _ROUTERS.rglob("*.py")
        if p.name != "_sse.py" and '"X-Accel-Buffering"' in p.read_text(encoding="utf-8")
    ]
    assert not hits, f"這些檔案自帶 SSE header，應改 import _sse.SSE_HEADERS：{hits}"

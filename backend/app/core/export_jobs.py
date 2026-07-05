"""通用導出背景 job：in-mem 進度快照 + SSE 消費 + 完成後取檔 + 協作式取消。

導出（xlsx 生成）原為同步端點（阻塞請求直到整份檔案組完才回 blob），大列表時前端只能空等、無進度。
改背景 job 後：`start_export` 立即回 job_id（不阻塞請求），builder 在背景 thread 逐步上報進度並輪詢
取消旗標，完成後把位元組存入 `_results` 供 download 端點取回。與 `prejudge_batch` / `upload_batch`
的 in-mem job 模式一致（單機夠用、重啟即清）。

與 prejudge 的差異：導出無逐筆 LLM 花費，故只提供「停止」（cancel），不做暫停/恢復。
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable

_log = logging.getLogger(__name__)

# job_id → 進度快照（JSON-safe，供 SSE 直接序列化推送）。
_jobs: dict[str, dict] = {}
# job_id → 完成後的檔案位元組（不入快照，避免大 bytes 進 JSON 序列化）；pop_result 取後即清。
_results: dict[str, bytes] = {}
# job_id → cancel Event（協作式取消：builder 於迴圈輪詢 ctx.check()，set 時拋 Cancelled 收斂）。
_cancels: dict[str, threading.Event] = {}
_lock = threading.Lock()


class Cancelled(Exception):
    """builder 偵測到取消旗標時由 `ExportCtx.check()` 拋出，`_run` 據此標 cancelled（非 error）。"""


class ExportCtx:
    """導出 builder 的進度/取消把手：builder 只依賴此介面，不觸及 job registry 內部結構。

    - `report(processed, total)`：上報進度（total 首次已知時一併帶入；未知期間 total=0＝準備中）。
    - `check()`：於建檔迴圈中定期呼叫，job 被取消時拋 `Cancelled` 使 builder 盡快收斂。
    """

    def __init__(self, job_id: str) -> None:
        self._job_id = job_id

    def report(self, processed: int, total: int) -> None:
        """更新進度快照（thread-safe；job 已不存在則靜默略過）。"""
        with _lock:
            snap = _jobs.get(self._job_id)
            if snap is None:
                return
            snap["processed"] = processed
            snap["total"] = total

    def check(self) -> None:
        """取消旗標已 set 時拋 Cancelled（builder 迴圈輪詢用；Python thread 無搶佔式中斷）。"""
        ev = _cancels.get(self._job_id)
        if ev is not None and ev.is_set():
            raise Cancelled()


# Builder 契約：接受 ExportCtx → 回檔案 bytes（過程中 report 進度 + check 取消）。
Builder = Callable[[ExportCtx], bytes]


def _new_snapshot(filename: str) -> dict:
    """初始 job 進度快照（欄位對齊前端 useExportJob SSE 消費端）。"""
    return {
        # 狀態機：running → done｜running → cancelling → cancelled｜error
        # （前端 SSE 見 done/error/cancelled 三終態停止串流）
        "status": "running",
        "total": 0,  # 0＝builder 尚未算出總量（前端顯示「準備中…」）
        "processed": 0,
        "filename": filename,
        "error": "",
    }


def _run(job_id: str, builder: Builder) -> None:
    """背景執行 builder：成功存 result bytes + 標 done；取消標 cancelled；其餘例外標 error。"""
    try:
        data = builder(ExportCtx(job_id))
        with _lock:
            snap = _jobs.get(job_id)
            if snap is None:  # 已被取消清除
                return
            _results[job_id] = data
            snap["status"] = "done"
            # total 未由 builder 設過時（如空資料）以 processed 收斂，避免前端卡在 0%
            snap["total"] = snap["total"] or snap["processed"]
            snap["processed"] = snap["total"]
    except Cancelled:
        _set_status(job_id, "cancelled")
    except Exception as e:  # noqa: BLE001  整份導出級失敗 → 標 error 供前端停串流並提示
        _log.exception("導出 job 失敗 job=%s", job_id)
        with _lock:
            snap = _jobs.get(job_id)
            if snap is not None:
                snap["status"] = "error"
                snap["error"] = str(e)
    finally:
        with _lock:
            _cancels.pop(job_id, None)


def _set_status(job_id: str, status: str) -> None:
    """設定 job 狀態（thread-safe）。"""
    with _lock:
        if job_id in _jobs:
            _jobs[job_id]["status"] = status


def start_export(builder: Builder, filename: str) -> str:
    """註冊並背景啟動一個導出 job；立即回 job_id（不阻塞請求）。

    Args:
        builder: 實際組檔的可呼叫物件，接 ExportCtx（過程回報進度 + 輪詢取消）→ 回檔案 bytes。
        filename: 建議下載檔名（存入快照供 download 端點 Content-Disposition；前端可自行覆寫）。

    Returns:
        job_id（前端據此連 SSE 串流進度、完成後 download）。
    """
    job_id = f"ex_{uuid.uuid4().hex[:12]}"
    with _lock:
        _jobs[job_id] = _new_snapshot(filename)
        _cancels[job_id] = threading.Event()
    threading.Thread(
        target=_run, args=(job_id, builder), name=f"export-{job_id}", daemon=True
    ).start()
    return job_id


def cancel_export(job_id: str) -> bool:
    """停止 job：set cancel 使 builder 下次 check() 收斂（轉 cancelled，不產出檔案）。

    回 True＝成功（job 存在且未達終態）。已在跑的建檔迴圈於下個 check 點中止（無搶佔式中斷）。
    """
    with _lock:
        snap = _jobs.get(job_id)
        if snap is None or snap["status"] in ("done", "error", "cancelled"):
            return False
        snap["status"] = "cancelling"
        ev = _cancels.get(job_id)
    if ev is not None:
        ev.set()
    return True


def get_job(job_id: str) -> dict | None:
    """取 job 進度快照複本（thread-safe）；不存在回 None（端點轉 404 / SSE 推 error）。"""
    with _lock:
        snap = _jobs.get(job_id)
        return dict(snap) if snap else None


def pop_result(job_id: str) -> bytes | None:
    """取回完成的檔案位元組並一次性清除（連同 job 快照）釋放記憶體；未完成/已取走回 None。"""
    with _lock:
        data = _results.pop(job_id, None)
        if data is not None:
            _jobs.pop(job_id, None)
        return data

"""通用導出背景 job：in-mem 進度快照 + SSE 消費 + 完成後取檔 + 協作式取消。

導出（xlsx 生成）原為同步端點（阻塞請求直到整份檔案組完才回 blob），大列表時前端只能空等、無進度。
改背景 job 後：`start_export` 立即回 job_id（不阻塞請求），builder 在背景 thread 逐步上報進度並輪詢
取消旗標，完成後把位元組存入 `_results` 供 download 端點取回。與 `prejudge_batch` / `upload_batch`
的 in-mem job 模式一致（單機夠用、重啟即清）；job 快照共用機制層見 `core.job_registry.JobStore`。

與 prejudge 的差異：導出無逐筆 LLM 花費，故只提供「停止」（cancel），不做暫停/恢復。
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Callable

from app.core.job_registry import JobStore

_log = logging.getLogger(__name__)

# 終態 job 快照的保留時窗（秒）：download pop 即清；使用者放棄下載時靠 start_export 的
# 惰性清掃回收，避免 in-mem registry 無界成長。
_TERMINAL_TTL_SECONDS = 3600
_TERMINAL_STATUSES = ("done", "error", "cancelled")

_store: JobStore = JobStore()
# job_id → 完成後的檔案位元組（不入快照，避免大 bytes 進 JSON 序列化）；pop_result 取後即清。
_results: dict[str, bytes] = {}
# job_id → cancel Event（協作式取消：builder 於迴圈輪詢 ctx.check()，set 時拋 Cancelled 收斂）。
_cancels: dict[str, threading.Event] = {}
# 專管 _results/_cancels（非 job 快照本身，故不進 JobStore；_store 內部另有自己的鎖）。
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

        def _apply(snap: dict) -> None:
            snap["processed"] = processed
            snap["total"] = total

        _store.mutate(self._job_id, _apply)

    def check(self) -> None:
        """取消旗標已 set 時拋 Cancelled（builder 迴圈輪詢用；Python thread 無搶佔式中斷）。"""
        with _lock:
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


def _mark_done(job_id: str) -> None:
    """標 done 並收斂進度（total 未由 builder 設過時以 processed 收斂，避免前端卡 0%）。"""

    def _apply(snap: dict) -> None:
        snap["status"] = "done"
        snap["done_at"] = time.time()
        snap["total"] = snap["total"] or snap["processed"]
        snap["processed"] = snap["total"]

    _store.mutate(job_id, _apply)


def _run(job_id: str, builder: Builder) -> None:
    """背景執行 builder：成功存 result bytes + 標 done；取消標 cancelled；其餘例外標 error。"""
    try:
        data = builder(ExportCtx(job_id))
        # job 可能已被取消清除（sweep）；仍存在才寫入結果並標 done。
        if _store.get(job_id) is not None:
            with _lock:
                _results[job_id] = data
            _mark_done(job_id)
    except Cancelled:
        _store.set_fields(job_id, status="cancelled")
    except Exception as e:  # noqa: BLE001  整份導出級失敗 → 標 error 供前端停串流並提示
        _log.exception("導出 job 失敗 job=%s", job_id)
        err_msg = str(e)
        _store.mutate(
            job_id,
            lambda snap: snap.update({"status": "error", "error": err_msg, "done_at": time.time()}),
        )
    finally:
        with _lock:
            _cancels.pop(job_id, None)


def _sweep_stale_jobs() -> None:
    """惰性回收超過 TTL 的終態 job（快照 + 未被取走的 bytes）。"""
    now = time.time()
    for jid, snap in _store.items_snapshot().items():
        if snap["status"] not in _TERMINAL_STATUSES:
            continue
        done_at = snap.get("done_at")
        if done_at is None:  # 終態但無時戳（如 cancelled）：現在補上，一個 TTL 後回收
            _store.set_fields(jid, done_at=now)
        elif now - done_at > _TERMINAL_TTL_SECONDS:
            _store.delete(jid)
            with _lock:
                _results.pop(jid, None)


def start_export(builder: Builder, filename: str) -> str:
    """註冊並背景啟動一個導出 job；立即回 job_id（不阻塞請求）。

    Args:
        builder: 實際組檔的可呼叫物件，接 ExportCtx（過程回報進度 + 輪詢取消）→ 回檔案 bytes。
        filename: 建議下載檔名（存入快照供 download 端點 Content-Disposition；前端可自行覆寫）。

    Returns:
        job_id（前端據此連 SSE 串流進度、完成後 download）。
    """
    job_id = f"ex_{uuid.uuid4().hex[:12]}"
    _sweep_stale_jobs()
    _store.put(job_id, _new_snapshot(filename))
    with _lock:
        _cancels[job_id] = threading.Event()
    threading.Thread(
        target=_run, args=(job_id, builder), name=f"export-{job_id}", daemon=True
    ).start()
    return job_id


def cancel_export(job_id: str) -> bool:
    """停止 job：set cancel 使 builder 下次 check() 收斂（轉 cancelled，不產出檔案）。

    回 True＝成功（job 存在且未達終態）。已在跑的建檔迴圈於下個 check 點中止（無搶佔式中斷）。
    """
    snap = _store.get(job_id)
    if snap is None or snap["status"] in _TERMINAL_STATUSES:
        return False
    _store.set_fields(job_id, status="cancelling")
    with _lock:
        ev = _cancels.get(job_id)
    if ev is not None:
        ev.set()
    return True


def get_job(job_id: str) -> dict | None:
    """取 job 進度快照複本（thread-safe）；不存在回 None（端點轉 404 / SSE 推 error）。"""
    return _store.get(job_id)


def pop_result(job_id: str) -> bytes | None:
    """取回完成的檔案位元組並一次性清除（連同 job 快照）釋放記憶體；未完成/已取走回 None。"""
    with _lock:
        data = _results.pop(job_id, None)
    if data is not None:
        _store.delete(job_id)
    return data


def mark_running_interrupted() -> list[str]:
    """graceful shutdown 收尾：把仍在 running 的導出 job 標 interrupted，回被標記的 job_id。

    SIGTERM 後 uvicorn drain 期間，輪詢進度的請求可拿到明確終態而非等連線被斷；
    daemon thread 隨 process 消逝無法回收（單 worker in-mem registry 既定限制）。
    """
    return _store.mark_interrupted(running_statuses=("running",), new_status="interrupted")

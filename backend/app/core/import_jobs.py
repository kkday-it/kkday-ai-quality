"""全庫資料包匯入背景 job：in-mem 進度快照 + SSE 消費。

與 export_jobs 同型（單機夠用、重啟即清），但語義較簡：匯入是**單一 DB 交易** truncate-then-load，
不提供中途取消（取消＝交易 rollback，等同未執行；故只需 running→done / error 兩終態 + 逐表進度）。

前端連 `/api/admin/import/stream?job_id=` 以 EventSource 消費快照，狀態達終態即停。
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Callable

_log = logging.getLogger(__name__)

# job_id → 進度快照（JSON-safe，供 SSE 直接序列化）。
_jobs: dict[str, dict] = {}
_lock = threading.Lock()

# 已達終態（done/error）的快照，逾此秒數才會被下一次 start_import 清掃——單機記憶體無界累積防護
# （匯入無下載步驟可掛清除時機，不同於 export_jobs 靠 pop_result 順帶回收，故需獨立 TTL 機制）。
_STALE_TTL_SECONDS = 1800


class ImportCtx:
    """匯入 runner 的進度把手：runner 只依賴此介面，不觸及 registry 內部結構。"""

    def __init__(self, job_id: str) -> None:
        self._job_id = job_id

    def report_table(self, name: str, rows: int, done_tables: int, total_tables: int) -> None:
        """回報「某表已灌入 rows 列、完成第 done_tables/total_tables 張」（thread-safe）。"""
        with _lock:
            snap = _jobs.get(self._job_id)
            if snap is None:
                return
            snap["current_table"] = name
            snap["done_tables"] = done_tables
            snap["total_tables"] = total_tables
            snap["inserted"][name] = rows


def _new_snapshot() -> dict:
    """初始匯入 job 快照（欄位對齊前端 SSE 消費端）。"""
    return {
        "status": "running",  # running → done | error
        "current_table": "",
        "done_tables": 0,
        "total_tables": 0,
        "inserted": {},  # {table: 已灌列數}
        "error": "",
        "_created_at": time.time(),  # 內部欄位，不對前端序列化（get_job 回傳前需濾除）
    }


def _prune_stale() -> None:
    """清掃已達終態且逾 TTL 的快照（呼叫端須持有 _lock）。"""
    cutoff = time.time() - _STALE_TTL_SECONDS
    stale = [
        jid
        for jid, snap in _jobs.items()
        if snap["status"] in ("done", "error") and snap.get("_created_at", cutoff) < cutoff
    ]
    for jid in stale:
        del _jobs[jid]


# runner 契約：接 ImportCtx → 回結果 dict（過程 report_table 上報進度）。
Runner = Callable[[ImportCtx], dict]


def _run(job_id: str, runner: Runner) -> None:
    """背景執行 runner：成功標 done（併入結果），例外標 error（附訊息供前端提示）。"""
    try:
        result = runner(ImportCtx(job_id))
        with _lock:
            snap = _jobs.get(job_id)
            if snap is None:
                return
            snap["status"] = "done"
            snap["inserted"] = result.get("inserted", snap["inserted"])
            snap["done_tables"] = len(result.get("tables", []))
            snap["total_tables"] = snap["done_tables"]
    except Exception as e:  # noqa: BLE001  整份匯入級失敗 → 標 error（交易已 rollback，DB 維持原狀）
        _log.exception("匯入 job 失敗 job=%s", job_id)
        with _lock:
            snap = _jobs.get(job_id)
            if snap is not None:
                snap["status"] = "error"
                snap["error"] = str(e)


def start_import(runner: Runner) -> str:
    """註冊並背景啟動一個匯入 job；立即回 job_id（不阻塞請求）。"""
    job_id = f"im_{uuid.uuid4().hex[:12]}"
    with _lock:
        _prune_stale()
        _jobs[job_id] = _new_snapshot()
    threading.Thread(
        target=_run, args=(job_id, runner), name=f"import-{job_id}", daemon=True
    ).start()
    return job_id


def get_job(job_id: str) -> dict | None:
    """取 job 進度快照複本（thread-safe，濾除內部欄位）；不存在回 None。"""
    with _lock:
        snap = _jobs.get(job_id)
        if snap is None:
            return None
        return {k: v for k, v in snap.items() if not k.startswith("_")}

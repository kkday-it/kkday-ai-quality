"""單次初判 job 的執行日誌（in-mem + ContextVar 綁定）——供前端抽屜 SSE 即時檢視。

與 prejudge_batch 併發模型對齊：_run 於背景 thread `bind(job_id)` → 每筆任務 copy_context()
快照攜帶 → ThreadPool worker 內 emit 自動歸入同一 job。僅小批量 job 收集（LOG_JOB_MAX_ITEMS），
大批量不收（記憶體/效能考量）。內容：各階段訊息 + LLM 突出資訊（輸入參數 / prompt 全文 /
原始輸出）；token 等機密絕不入日誌。entry 索引穩定（滿了丟「新」條目並計數，不移舊），
SSE 端點以 offset 增量讀取（見 v1/prejudge.py prejudge_log_stream）。
"""

from __future__ import annotations

import threading
import time
from contextvars import ContextVar

# 僅此筆數以下的 job 收集日誌（單筆/小批選取＝抽屜檢視場景；大批量不收，防 prompt 全文撐爆記憶體）
LOG_JOB_MAX_ITEMS = 20
_MAX_ENTRIES = 2000  # 單 job 條目上限：超出丟「新」條目並計 dropped（索引穩定，SSE offset 不失效）
_MAX_JOBS = 50  # 保留 job 數上限：FIFO 淘汰最舊（dict 插入序）

_logs: dict[str, dict] = {}  # job_id → {"entries": [dict], "dropped": int, "done": bool}
_lock = threading.Lock()
_job: ContextVar[str | None] = ContextVar("judge_run_log_job", default=None)


def bind(job_id: str) -> None:
    """建立 job 日誌容器並綁定當前 context（copy_context 派工後 worker 自動繼承歸屬）。"""
    with _lock:
        if job_id not in _logs:
            _logs[job_id] = {"entries": [], "dropped": 0, "done": False}
            while len(_logs) > _MAX_JOBS:
                _logs.pop(next(iter(_logs)))
    _job.set(job_id)


def emit(
    kind: str, stage: str, message: str, data: dict | None = None, *, label: str | None = None
) -> None:
    """追加一筆日誌（未 bind＝no-op；任何失敗不阻斷初判）。

    kind：stage（一般階段）｜llm_request｜llm_prompt｜llm_response｜llm_note｜error。
    label：同一次 LLM 調用的分組鍵（前端據此把 request/prompt/response 聚合成一個 tab；
        polarity / C-1..C-6 各為一組）；未給則前端回退用 stage。
    """
    job_id = _job.get()
    if not job_id:
        return
    try:
        entry: dict = {
            "ts": round(time.time(), 3),
            "kind": kind,
            "stage": stage,
            "message": message,
        }
        if label:
            entry["label"] = label
        if data:
            entry["data"] = data
        with _lock:
            box = _logs.get(job_id)
            if box is None:
                return
            if len(box["entries"]) >= _MAX_ENTRIES:
                box["dropped"] += 1
                return
            box["entries"].append(entry)
    except Exception:  # noqa: BLE001  日誌純輔助，絕不阻斷初判
        pass


def finish(job_id: str) -> None:
    """標記 job 日誌收集結束（SSE 讀盡即關閉串流）；有丟棄時補一筆截斷摘要。"""
    with _lock:
        box = _logs.get(job_id)
        if box is None:
            return
        if box["dropped"]:
            box["entries"].append(
                {
                    "ts": round(time.time(), 3),
                    "kind": "stage",
                    "stage": "job",
                    "message": f"（日誌已達上限，另有 {box['dropped']} 筆未收錄）",
                }
            )
        box["done"] = True


def read(job_id: str, offset: int = 0) -> tuple[list[dict], bool, bool]:
    """增量讀取日誌：回 (entries[offset:] 複本, done, exists)。job 不存在＝(…, …, False)。"""
    with _lock:
        box = _logs.get(job_id)
        if box is None:
            return [], False, False
        return list(box["entries"][offset:]), box["done"], True

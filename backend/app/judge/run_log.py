"""單次初判 job 的執行日誌（in-mem + ContextVar 綁定）——逐筆落庫回看 + 小批量 SSE 即時檢視。

與 prejudge_batch 併發模型對齊：_run 於背景 thread `bind(job_id)` → 每筆任務 copy_context()
快照攜帶 → ThreadPool worker 內 emit 自動歸入同一 job/評論。內容：各階段訊息 + LLM 突出資訊
（輸入參數 / prompt 全文 / 原始輸出）；token 等機密絕不入日誌。

兩條去處，生命週期不同（**這是本模組唯一需要記住的事**）：

1. **逐筆緩衝 `_pending`（所有 job 皆收）**：emit 依當前 `_item` 歸進該評論的桶；`_work_one` 判完
   即 `take()` 取走落庫並清空記憶體 → 佔用是 O(併發數) 而非 O(批量筆數)，故不分批量大小全收。
2. **job 級佇列 `_logs`（僅小批量 job）**：供 SSE 即時推送，需要「索引穩定」才能以 offset 增量讀
   （滿了丟「新」條目並計數，不移舊；見 v1/prejudge.py prejudge_log_stream）。大批量不建此佇列
   ——即時看數萬筆混排日誌無意義，且它得整批留到結束才能釋放。
"""

from __future__ import annotations

import threading
import time
from contextvars import ContextVar

# 僅此筆數以下的 job 建 SSE 即時佇列（單筆/小批＝抽屜盯著看的場景）；大批量只走逐筆落庫（見檔頭）。
LOG_LIVE_MAX_ITEMS = 20
_MAX_ENTRIES = 2000  # 單 job 佇列上限：超出丟「新」條目並計 dropped（索引穩定，SSE offset 不失效）
_MAX_JOBS = 50  # 保留 job 數上限：FIFO 淘汰最舊（dict 插入序）
# 單筆評論的條目上限：正常約 13 條（流程 4 + LLM 7 域各 3 段），設 200 純為異常迴圈的保險絲。
_MAX_ITEM_ENTRIES = 200

_logs: dict[str, dict] = {}  # job_id → {"entries": [dict], "dropped": int, "done": bool}（SSE 用）
# job_id → {source_id: [entry]}：逐筆落庫緩衝，`take()` 取走即清；job 級事件歸在 "" 這個桶。
_pending: dict[str, dict[str, list[dict]]] = {}
_lock = threading.Lock()
_job: ContextVar[str | None] = ContextVar("judge_run_log_job", default=None)
# 當前處理中的 item（source_id）——emit 自動蓋章 entry["item_id"]，供前端按評論分組批量日誌；
# 與 _job 同走 copy_context 快照攜帶（含 prejudge 六域 ThreadPool），job 級事件未 bind＝不帶欄。
_item: ContextVar[str | None] = ContextVar("judge_run_log_item", default=None)


def bind(job_id: str, *, live: bool = True) -> None:
    """綁定當前 context 到此 job（copy_context 派工後 worker 自動繼承歸屬）。

    Args:
        job_id: 初判任務 id。
        live: 是否另建 SSE 即時佇列（僅小批量；大批量傳 False，只走逐筆落庫緩衝）。
    """
    with _lock:
        _pending.setdefault(job_id, {})
        if live and job_id not in _logs:
            _logs[job_id] = {"entries": [], "dropped": 0, "done": False}
            while len(_logs) > _MAX_JOBS:
                _logs.pop(next(iter(_logs)))
    _job.set(job_id)


def bind_item(item_id: str | None) -> None:
    """綁定當前 context 的 item 歸屬（copy_context 派工的 worker 各自隔離；None＝解除）。"""
    _item.set(item_id or None)


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
        item_id = _item.get()
        if item_id:
            entry["item_id"] = item_id
        if data:
            entry["data"] = data
        with _lock:
            # ① 逐筆落庫緩衝（所有 job）：歸進當前評論的桶，未綁定 item 的 job 級事件歸 ""
            bucket = _pending.setdefault(job_id, {}).setdefault(item_id or "", [])
            if len(bucket) < _MAX_ITEM_ENTRIES:
                bucket.append(entry)
            # ② SSE 即時佇列（僅小批量 job 有建）
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


def take(job_id: str, item_id: str | None) -> list[dict]:
    """取走某評論已累積的條目並清空該桶（呼叫端負責落庫）——這是記憶體 O(併發數) 的關鍵。

    item_id 傳 None/空＝取 job 級事件桶（任務啟動/收尾等不屬於任何評論的條目）。
    """
    with _lock:
        return _pending.get(job_id, {}).pop(item_id or "", [])


def drop_job(job_id: str) -> None:
    """丟棄該 job 殘留的逐筆緩衝（正常路徑各桶已被 take 取空；此為異常中止的兜底清理）。"""
    with _lock:
        _pending.pop(job_id, None)


def read(job_id: str, offset: int = 0) -> tuple[list[dict], bool, bool]:
    """增量讀取日誌：回 (entries[offset:] 複本, done, exists)。job 不存在＝(…, …, False)。"""
    with _lock:
        box = _logs.get(job_id)
        if box is None:
            return [], False, False
        return list(box["entries"][offset:]), box["done"], True

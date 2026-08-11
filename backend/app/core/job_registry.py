"""in-mem job 進度 registry 共用機制層：dict + lock + 快照 + 終態掃描。

背景：export_jobs/import_jobs/judge.prejudge_batch/judge.ingest.upload_batch/judge.prompt_debug_batch
等各自手刻同型骨架（module 級 dict+lock、深拷貝快照、終態掃描），遠超 Rule of Three。
本模組只承載各套真正共用的最小機制層——「怎麼安全存取一份 dict」；不管 snapshot 長什麼樣、
不管暫停/取消。控制流（AIMD 自適應併發治理、暫停/恢復 gate、cancel Event 等）留在各自呼叫端模組，
不進此基底——複製整套控制流複雜度只會增加不必要負擔。

Composition，非強制繼承：各模組維持自己的 public API（start_job/get_job/cancel_job...）簽名完全
不變，只是內部把 `_jobs: dict` + `_lock` 換成一個模組級 `_store = JobStore()`。
"""

from __future__ import annotations

import copy
import json
import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Generic, TypeVar

_log = logging.getLogger(__name__)

# 終態 SSOT：原本 export_jobs 與 prompt_debug_batch 各寫一份相同的 tuple，
# 而 import_jobs / upload_batch 的 SSE 又各自硬編碼了不完整的子集（漏 cancelled/interrupted），
# 導致收尾時被標成 interrupted 的 job 串流永不結束。集中在此，加新終態只改一處。
TERMINAL_STATUSES: tuple[str, ...] = ("done", "error", "cancelled", "interrupted")


def is_terminal(status: str) -> bool:
    """job 狀態是否為終態（不會再變動）。"""
    return status in TERMINAL_STATUSES


T = TypeVar("T", bound=dict)
R = TypeVar("R")


class JobStore(Generic[T]):
    """單一 registry 的 thread-safe 存取層（記憶體為主，可選磁碟持久化跨行程留痕）。

    Args:
        persist_dir: 給定時啟用 `persist()`/`restore()`——**只用來讓行程重啟後的 job 有話可說**，
            不是完整持久化：背景 thread 本身隨行程消逝無法復活，落盤的是「它被中斷了」這個事實。
            不給＝純記憶體（其餘既有呼叫端行為不變）。
    """

    def __init__(self, persist_dir: Path | None = None) -> None:
        self._jobs: dict[str, T] = {}
        self._lock = threading.Lock()
        self._persist_dir = persist_dir

    def put(self, job_id: str, snapshot: T) -> None:
        """註冊一筆新 job 快照（覆蓋同 id 既有者）。"""
        with self._lock:
            self._jobs[job_id] = snapshot

    def get(self, job_id: str) -> T | None:
        """回傳快照深拷貝複本（thread-safe）；不存在回 None。

        統一深拷貝語義：修掉現有各套 get_job 深拷貝粒度不一致的問題（部分套件過去只淺拷貝，
        呼叫端會意外持有內部巢狀結構的共享引用）。
        """
        with self._lock:
            snap = self._jobs.get(job_id)
            return copy.deepcopy(snap) if snap is not None else None

    def mutate(self, job_id: str, fn: Callable[[T], R]) -> R | None:
        """under lock 呼叫 `fn(snapshot)` 並原子回傳其結果；job 不存在則不呼叫 fn，回 None。

        原子性是重點：呼叫端若需要「檢查條件才修改」（如 pause_job 的 status=='running' 才轉
        paused），必須讓 fn 內部做「檢查+修改+回傳是否成功」，不能先 get() 檢查、再另外呼叫
        set_fields()——分兩步會在檢查與修改之間開一個競態窗口（例如其間被另一 thread 轉了
        cancelling）。若呼叫端不需要回傳值可忽略。
        """
        with self._lock:
            snap = self._jobs.get(job_id)
            if snap is None:
                return None
            return fn(snap)

    def set_fields(self, job_id: str, **fields: object) -> bool:
        """常見的「設幾個欄位」捷徑；job 不存在回 False。"""
        return self.mutate(job_id, lambda snap: snap.update(fields))

    def delete(self, job_id: str) -> None:
        """移除一筆（存在即刪，不存在為 no-op）。"""
        with self._lock:
            self._jobs.pop(job_id, None)

    def pop(self, job_id: str) -> T | None:
        """取出並移除（export_jobs 的 pop_result 用途）；不存在回 None。"""
        with self._lock:
            return self._jobs.pop(job_id, None)

    def mark_interrupted(
        self,
        running_statuses: tuple[str, ...] = ("running",),
        new_status: str = "interrupted",
    ) -> list[str]:
        """graceful shutdown 收尾：把仍在指定狀態的 job 標記為終態；回被標記的 job_id 清單。"""
        with self._lock:
            hit = [
                jid for jid, snap in self._jobs.items() if snap.get("status") in running_statuses
            ]
            for jid in hit:
                self._jobs[jid]["status"] = new_status
            return hit

    def sweep_terminal(
        self,
        ttl_seconds: float,
        terminal_statuses: tuple[str, ...],
        created_at_key: str = "_created_at",
    ) -> None:
        """TTL 回收：清掉已達終態且逾時的快照（呼叫端決定何時觸發，通常在 start 前呼叫一次）。"""
        cutoff = time.time() - ttl_seconds
        with self._lock:
            stale = [
                jid
                for jid, snap in self._jobs.items()
                if snap.get("status") in terminal_statuses
                and snap.get(created_at_key, cutoff) < cutoff
            ]
            for jid in stale:
                del self._jobs[jid]

    def items_snapshot(self) -> dict[str, T]:
        """回傳全體 job 的深拷貝複本字典（供既有邏輯需要一次性遍歷全體時使用）。"""
        with self._lock:
            return copy.deepcopy(self._jobs)

    def persist(self, statuses: tuple[str, ...] | None = None) -> int:
        """把（指定狀態的）快照寫入 `persist_dir/jobs.json`；未設 persist_dir 為 no-op。

        用於 graceful shutdown 收尾：`mark_interrupted()` 標完狀態後呼叫，下個行程才拿得到
        「這個 job 被行程重啟打斷了」。不落盤的話快照隨行程消失，端點只能回語焉不詳的
        「job 不存在」，使用者無從得知自己的導出為何失敗。

        Args:
            statuses: 只寫這些狀態的 job；None＝全寫。

        Returns:
            實際寫出的筆數（persist_dir 未設或寫檔失敗回 0）。
        """
        if self._persist_dir is None:
            return 0
        with self._lock:
            data = {
                jid: snap
                for jid, snap in self._jobs.items()
                if statuses is None or snap.get("status") in statuses
            }
        try:
            self._persist_dir.mkdir(parents=True, exist_ok=True)
            (self._persist_dir / "jobs.json").write_text(
                json.dumps(data, ensure_ascii=False), encoding="utf-8"
            )
        except OSError:  # 落盤失敗不該拖垮 shutdown：留痕是加分項，不是關機的前提
            _log.exception("job 快照落盤失敗 dir=%s", self._persist_dir)
            return 0
        return len(data)

    def restore(self) -> int:
        """行程啟動時讀回落盤快照；**只補進當前不存在的 id**，不覆蓋記憶體現況。

        讀完即刪檔——這些快照的唯一用途是回答「上個行程留下的那個 job 怎麼了」，
        留著會在下次重啟時被誤當成本次的殘留。

        Returns:
            讀入筆數（無檔案 / 格式壞掉回 0）。
        """
        if self._persist_dir is None:
            return 0
        f = self._persist_dir / "jobs.json"
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            f.unlink(missing_ok=True)
        except (OSError, ValueError):  # 檔案不存在屬常態（乾淨啟動）；壞檔不值得擋住開機
            return 0
        if not isinstance(data, dict):
            return 0
        with self._lock:
            fresh = {jid: snap for jid, snap in data.items() if jid not in self._jobs}
            self._jobs.update(fresh)  # type: ignore[arg-type]
        return len(fresh)

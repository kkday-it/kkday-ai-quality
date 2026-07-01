"""初判歸因批量編排：in-mem job registry + ThreadPool 併發判決 → 落庫 + 累計花費。

前端「進行初判歸因」→ `start_job` 立即回 job_id（背景派工），前端輪詢 `get_job` 拿進度。

併發模型（為何這樣做）：
- judge 路徑靠兩個 contextvar 取設定——`settings.current()`（effective LLM dict）與
  `client._usage_sink`（token 用量回報）。ThreadPool worker 是**另一條 thread**，主/背景 thread
  set 的 contextvar 對它不可見；故每筆任務以 `copy_context()` 快照攜帶（快照在已 set 好兩個
  contextvar 的背景 thread 產生），worker 內 `ctx.run(...)` 即自動繼承，與 client.py sink 註解一致。
- 全域 `BoundedSemaphore(prejudge_max_workers)`：單 job 內併發即等於 pool 大小不受影響；多 job
  疊加時把「同時在跑的 LLM 呼叫數」收斂到此上限（對齊 config.env.prejudge_max_workers 語義）。
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context

from app.core import db, pricing
from app.core import settings as app_settings
from app.core.config import env
from app.judge import prejudge
from app.judge.llm import client

_log = logging.getLogger(__name__)

# in-mem job 進度快照（單機夠用，重啟即清；job_id → snapshot dict）。
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()

# 全域併發閘：多 job 疊加時把同時在跑的判決收斂到 prejudge_max_workers（見檔頭說明）。
_sem = threading.BoundedSemaphore(env.prejudge_max_workers)

# 撈 intake item 的分塊大小：避免 scope=all（~8 萬 item_id）一次塞進 IN 子句撐爆 SQL。
_FETCH_CHUNK = 500


def _new_snapshot(total: int, model: str) -> dict:
    """初始 job 進度快照（欄位逐一對齊前端 getPrejudgeStatus 消費端）。"""
    return {
        "status": "running",  # running → done / error（前端輪詢見此二值停止）
        "total": total,
        "processed": 0,
        "ok": 0,
        "failed": 0,
        "model": model,
        "total_tokens": 0,
        "cost_usd": 0.0,
    }


def _normalize_raw(item: dict) -> dict:
    """把 intake_items 列的 `raw`（JSON 字串）就地解成 dict。

    prejudge 的 `_evidence_cap` / `_text_of` 會對 `item["raw"]` 直接 `.get(...)`，而 db 存的是
    `json.dumps` 後的字串——不先解會在負向供應商判決路徑 AttributeError。於邊界層正規化，
    prejudge 引擎維持零改動。
    """
    raw = item.get("raw")
    if isinstance(raw, str):
        try:
            item["raw"] = json.loads(raw)
        except (ValueError, TypeError):
            item["raw"] = {}
    elif raw is None:
        item["raw"] = {}
    return item


def _bump(job_id: str, *, ok: bool, tokens: int = 0, cost: float = 0.0) -> None:
    """單筆判決完成後累加進度（thread-safe；processed / ok|failed）。"""
    with _jobs_lock:
        snap = _jobs.get(job_id)
        if snap is None:
            return
        snap["processed"] += 1
        snap["ok" if ok else "failed"] += 1


def _work_one(job_id: str, item: dict, model: str, source: str | None) -> None:
    """判決單筆 → 落庫；例外計 failed 不中斷整批（全域 Semaphore 收斂併發）。"""
    with _sem:
        try:
            f = prejudge.to_finding(_normalize_raw(item), model=model)
            db.insert_finding(f, source or item.get("source") or "")
            _bump(job_id, ok=True)
        except Exception:  # noqa: BLE001  單筆失敗隔離，不讓一筆炸掉整批
            _log.exception("初判歸因單筆失敗 job=%s item=%s", job_id, item.get("item_id"))
            _bump(job_id, ok=False)


def _run(job_id: str, item_ids: list[str], eff: dict, model: str, source: str | None = None) -> None:
    """背景執行整批判決：注入設定 contextvar → 分塊撈 item → ThreadPool 併發 → 標記結束。"""
    # 在背景 thread 的 context 內 set 好兩個 contextvar，稍後每筆任務 copy_context 快照即帶上。
    app_settings.set_current(eff)

    def _sink(m: str, prompt: int, completion: int) -> None:
        """token 用量回報：累計 total_tokens 並依實際模型單價加總 cost_usd（thread-safe）。"""
        with _jobs_lock:
            snap = _jobs.get(job_id)
            if snap is None:
                return
            snap["total_tokens"] += prompt + completion
            snap["cost_usd"] = round(snap["cost_usd"] + pricing.cost_usd(m, prompt, completion), 6)

    client.set_usage_sink(_sink)
    try:
        with ThreadPoolExecutor(max_workers=env.prejudge_max_workers) as ex:
            for start in range(0, len(item_ids), _FETCH_CHUNK):
                chunk = item_ids[start : start + _FETCH_CHUNK]
                for item in db.get_items_by_ids(chunk, source):
                    ctx = copy_context()  # 每筆獨立快照（同一 Context 不可並發 run）
                    ex.submit(ctx.run, _work_one, job_id, item, model, source)
            # with 區塊結束 → shutdown(wait=True) 等所有 worker 完成
        _set_status(job_id, "done")
    except Exception:  # noqa: BLE001  整批級失敗（如 DB 連線斷）→ 標 error 供前端停輪詢
        _log.exception("初判歸因批量任務失敗 job=%s", job_id)
        _set_status(job_id, "error")
    finally:
        client.set_usage_sink(None)


def _set_status(job_id: str, status: str) -> None:
    """設定 job 終態（thread-safe）。"""
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id]["status"] = status


def start_job(item_ids: list[str], eff: dict, model: str, source: str | None = None) -> str:
    """註冊並背景啟動一個初判歸因批量任務；立即回 job_id（不阻塞請求）。

    Args:
        item_ids: 判決標的 item_id 清單（端點已解析：顯式選取 / scope=all 未判集合）。
        eff: effective LLM dict（settings.effective_llm_dict 產；含 model/token/reasoning）。
        model: 主判決模型名（Stage2/2b；stub 模式引擎自走啟發式）。
        source: 來源 code（穿透至 get_items_by_ids 選表 + insert_finding 記錄來源；
            None＝沿用 intake_items 舊行為）。

    Returns:
        job_id（前端據此輪詢 get_job）。
    """
    job_id = f"pj_{uuid.uuid4().hex[:12]}"
    with _jobs_lock:
        _jobs[job_id] = _new_snapshot(len(item_ids), model)
    threading.Thread(
        target=_run,
        args=(job_id, item_ids, eff, model, source),
        name=f"prejudge-{job_id}",
        daemon=True,
    ).start()
    return job_id


def get_job(job_id: str) -> dict | None:
    """取 job 進度快照複本（thread-safe）；不存在回 None（端點轉 404）。"""
    with _jobs_lock:
        snap = _jobs.get(job_id)
        return dict(snap) if snap else None

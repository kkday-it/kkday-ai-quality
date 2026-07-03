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
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
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

# 每 job 一組協作式控制旗標（暫停/取消）：
# - gate: set＝可跑、clear＝暫停；提交迴圈每筆前 `gate.wait()`，暫停時阻塞、恢復即續。
# - cancel: set＝停止；迴圈檢查到即 break，drain 已在跑的 future（Python thread 無法搶佔式中斷，
#   已發出的 LLM 呼叫最壞等 llm_timeout=60s 收斂）。取消時一併 gate.set() 喚醒被暫停的迴圈。
# 與 _jobs 同生命週期，_jobs_lock 保護 dict 存取（Event 自身 thread-safe）。
_controls: dict[str, dict[str, threading.Event]] = {}

# 全域併發閘：多 job 疊加時把同時在跑的判決收斂到 prejudge_max_workers（見檔頭說明）。
_sem = threading.BoundedSemaphore(env.prejudge_max_workers)

# 撈 intake item 的分塊大小：避免 scope=all（~8 萬 item_id）一次塞進 IN 子句撐爆 SQL。
_FETCH_CHUNK = 500


def _new_snapshot(total: int, model: str) -> dict:
    """初始 job 進度快照（欄位逐一對齊前端 getPrejudgeStatus 消費端）。"""
    return {
        # 狀態機：running ⇌ paused → done｜running/paused → cancelling → cancelled｜error
        # （前端 SSE 見 done/error/cancelled 三終態停止串流）
        "status": "running",
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
    """判決單筆 → 落庫；例外計 failed 不中斷整批（全域 Semaphore 收斂併發）。

    item 為來源表列（源欄名）。先注入 canonical content + source_id + source（供 prejudge 引擎），
    全 5 來源統一走 to_findings（1:N 多歸因），以 replace_source_findings 整組替換 (source, source_id)
    舊列（重判冪等、保留 true_label）。
    """
    from app.core import source_mapping as _srcmap
    from app.core import source_registry as _reg

    with _sem:
        try:
            src = source or ""
            spec = _reg.spec_for(src)
            canon = _srcmap.normalize_row(src, item) if src in _srcmap.sources() else {}
            source_id = str(item.get(spec.natural_key) or "") if spec else ""
            norm = dict(item)
            norm["source"] = src
            norm["source_id"] = source_id
            norm["content"] = canon.get("content") or ""  # 判決主輸入（各來源源欄→canonical）
            norm["prod_oid"] = canon.get("prod_oid") or ""
            norm["order_oid"] = canon.get("order_oid") or ""
            norm["raw"] = item  # 供 _evidence_cap 讀 order_oid
            findings = prejudge.to_findings(norm, model=model)
            db.replace_source_findings(src, source_id, findings)
            _bump(job_id, ok=True)
        except Exception:  # noqa: BLE001  單筆失敗隔離，不讓一筆炸掉整批
            _log.exception("初判歸因單筆失敗 job=%s item=%s", job_id, item.get("item_id"))
            _bump(job_id, ok=False)


def _run(job_id: str, item_ids: list[str], eff: dict, model: str, source: str | None = None) -> None:
    """背景執行整批判決：注入設定 contextvar → 分塊撈 item → 有背壓地逐筆提交（支援暫停/取消）→ 標記結束。"""
    # 在背景 thread 的 context 內 set 好兩個 contextvar，稍後每筆任務 copy_context 快照即帶上。
    app_settings.set_current(eff)

    def _sink(m: str, prompt: int, completion: int, cached: int = 0) -> None:
        """token 用量回報：累計 total_tokens 並依模型單價加總 cost_usd（cached 部分折扣計；thread-safe）。"""
        with _jobs_lock:
            snap = _jobs.get(job_id)
            if snap is None:
                return
            snap["total_tokens"] += prompt + completion
            snap["cost_usd"] = round(snap["cost_usd"] + pricing.cost_usd(m, prompt, completion, cached), 6)

    client.set_usage_sink(_sink)
    ctrl = _controls.get(job_id, {})
    gate, cancel = ctrl.get("gate"), ctrl.get("cancel")
    max_workers = env.prejudge_max_workers
    try:
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            in_flight: set[Future] = set()
            for start in range(0, len(item_ids), _FETCH_CHUNK):
                if cancel and cancel.is_set():
                    break
                chunk = item_ids[start : start + _FETCH_CHUNK]
                for item in db.get_items_by_ids(chunk, source):
                    # 暫停閘：暫停時阻塞於此，恢復（或取消 gate.set 喚醒）即續
                    if gate:
                        gate.wait()
                    if cancel and cancel.is_set():
                        break
                    # 背壓：in-flight future 維持 ≤ max_workers（避免 scope=all 一次塞數萬 future 撐爆記憶體；
                    # 也讓暫停時 processed 於已提交批收斂後即停增，符合「暫停即停」語義）
                    while len(in_flight) >= max_workers:
                        _, in_flight = wait(in_flight, return_when=FIRST_COMPLETED)
                    c = copy_context()  # 每筆獨立快照（同一 Context 不可並發 run）
                    in_flight.add(ex.submit(c.run, _work_one, job_id, item, model, source))
                if cancel and cancel.is_set():
                    break
            wait(in_flight)  # drain 剩餘（正常跑完 / 取消後已提交的收斂；with 結束亦 shutdown(wait=True)）
        _set_status(job_id, "cancelled" if (cancel and cancel.is_set()) else "done")
    except Exception:  # noqa: BLE001  整批級失敗（如 DB 連線斷）→ 標 error 供前端停輪詢
        _log.exception("初判歸因批量任務失敗 job=%s", job_id)
        _set_status(job_id, "error")
    finally:
        client.set_usage_sink(None)
        _drop_controls(job_id)


def _set_status(job_id: str, status: str) -> None:
    """設定 job 狀態（thread-safe）。"""
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id]["status"] = status


def _drop_controls(job_id: str) -> None:
    """job 結束後清理控制旗標（避免 _controls 無限增長；job 快照仍保留供前端讀終態）。"""
    with _jobs_lock:
        _controls.pop(job_id, None)


def pause_job(job_id: str) -> bool:
    """暫停 job：清 gate 使提交迴圈阻塞；status→paused。回 True＝成功（job 存在且 running）。"""
    with _jobs_lock:
        snap = _jobs.get(job_id)
        if snap is None or snap["status"] != "running":
            return False
        snap["status"] = "paused"
        ctrl = _controls.get(job_id)
    if ctrl:
        ctrl["gate"].clear()
    return True


def resume_job(job_id: str) -> bool:
    """恢復 job：set gate 使提交迴圈續跑；status→running。回 True＝成功（job 存在且 paused）。"""
    with _jobs_lock:
        snap = _jobs.get(job_id)
        if snap is None or snap["status"] != "paused":
            return False
        snap["status"] = "running"
        ctrl = _controls.get(job_id)
    if ctrl:
        ctrl["gate"].set()
    return True


def cancel_job(job_id: str) -> bool:
    """停止 job：set cancel + gate（喚醒暫停中迴圈）；status→cancelling（drain 後由 _run 轉 cancelled）。

    回 True＝成功（job 存在且未達終態）。已判 finding 已落庫保留；剩餘未判可事後重跑。
    """
    with _jobs_lock:
        snap = _jobs.get(job_id)
        if snap is None or snap["status"] in ("done", "error", "cancelled"):
            return False
        snap["status"] = "cancelling"
        ctrl = _controls.get(job_id)
    if ctrl:
        ctrl["cancel"].set()
        ctrl["gate"].set()  # 喚醒被暫停阻塞的提交迴圈，使其看到 cancel 並 break
    return True


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
        gate = threading.Event()
        gate.set()  # 預設可跑（暫停時清除）
        _controls[job_id] = {"gate": gate, "cancel": threading.Event()}
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

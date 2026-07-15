"""歸因列表 Prompt 測試沙盒背景 job：in-mem 進度 + run_log 綁定 + 結束落庫快照。

比照 `prejudge_batch.py` 的 job registry pattern（`start` 立即回 job_id，前端輪詢 `get_job` 拿
進度），但刻意不含暫停/取消/自適應併發/計費 sink——沙盒測試是調適用途的小規模操作，不需要正式批量
判決管線的這些控制項，複製過來只會增加不必要的複雜度。

與 `prejudge_batch` 最大差異：結果不落 `judgments`/`judgment_history`（正式歸因），只落獨立的
`prompt_sandbox_runs`（見 `core/db/tables.py`），確保測試歷史與正式初判完全分離。job 結束時把
`run_log` 快照（`run_log.read`）一併存進該筆歷史，供事後回看當時的完整 LLM log（run_log 本身純
記憶體、job 淘汰後不可回溯，落庫快照是唯一持久化管道）。
"""

from __future__ import annotations

import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context

from app.core import db
from app.core import settings as app_settings
from app.judge import prompt_eval, run_log
from app.judge.llm import client

_log = logging.getLogger(__name__)

# in-mem job 進度快照（單機夠用，重啟即清；job_id → snapshot dict）。
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()

# 筆數間併發（每筆內部域 prompt 已用自己的 ThreadPool 並行，見 prompt_eval.domain_verdicts）；
# 沙盒測試非正式判決量級，固定小併發即可，不比照 prejudge_batch 的 per-model/自適應併發。
_MAX_WORKERS = 4


def _guard_stub(eff: dict) -> None:
    """無條件拒絕 stub 模式（不只正式環境）：比照 `prompt_eval.classify_one` 既有慣例——
    Prompt 測試沙盒是「看 prompt 實際判得怎樣」的調適工具，stub 假結果會誤導判斷，dev 環境
    零 key 時也不例外（與 `prejudge_batch` 正式批量判決刻意放行 dev stub 的定位不同）。
    """
    if not app_settings.resolve_provider_token(eff):
        raise ValueError("目前配置無可用 LLM token（stub 模式），拒絕以假結果執行 Prompt 測試沙盒")


def start(
    source: str,
    source_ids: list[str],
    prompt_ids: list[str],
    eff: dict,
    *,
    scope: str,
    triggered_by: str = "",
) -> str:
    """啟動沙盒測試背景 job，立即回 job_id（前端輪詢 `get_job` 拿進度）。

    Args:
        source: 來源 code（如 product_reviews）。
        source_ids: 受測 item 清單（scope=single 時長度 1）。選取筆數不設上限（使用者決策：大批量
            靠 `run_log` 既有 dropped 機制截斷 log，結果仍逐筆落庫）。
        prompt_ids: 使用者勾選的 prompt 子集（polarity / C-1..C-6）。
        eff: effective LLM 設定（呼叫端已解析，見 `app_settings.effective_llm_dict`）。
        scope: single（單列觸發）/ selection（工具列勾選多筆觸發）——落庫供歷史列表分辨來源。
        triggered_by: 觸發人 email。

    Returns:
        job_id（`psbxjob_` 前綴；與測試結束落庫的 `run_id` 不同——job_id 供進度輪詢/log 綁定）。

    Raises:
        ValueError: stub 模式（無可用 LLM token）——無條件拒跑，dev 亦不例外。
    """
    _guard_stub(eff)
    job_id = f"psbxjob_{uuid.uuid4().hex}"
    with _jobs_lock:
        _jobs[job_id] = {"status": "running", "total": len(source_ids), "done": 0, "run_id": None}
    threading.Thread(
        target=_run,
        args=(job_id, source, source_ids, prompt_ids, eff, scope, triggered_by),
        daemon=True,
    ).start()
    return job_id


def _one(source: str, source_id: str, prompt_ids: list[str], model: str) -> dict:
    """單筆：組 item → `sandbox_classify`。獨立函式供 ThreadPoolExecutor 提交（copy_context 攜帶
    設定 contextvar，見 `_run`）。單筆組裝/判決失敗（如找不到該則評論）讓例外往上拋，由 `_run` 的
    `future.result()` 呼叫端接住記錄，不擋同批其他筆。
    """
    item = prompt_eval._build_sandbox_item(source, source_id)
    return prompt_eval.sandbox_classify(item, prompt_ids, model)


def _run(
    job_id: str,
    source: str,
    source_ids: list[str],
    prompt_ids: list[str],
    eff: dict,
    scope: str,
    triggered_by: str,
) -> None:
    """背景執行：bind run_log（不設筆數上限）→ 逐筆並行 `sandbox_classify` → 結束落
    `prompt_sandbox_runs` 快照（含 results + log 完整快照）。
    """
    app_settings.set_current(eff)  # 背景 thread set 好 contextvar，供 copy_context 快照攜帶
    client.set_llm_cache_read(False)  # 沙盒測試量測真實行為（同 classify-one/prompt-eval 既有慣例）
    client.set_usage_context({"job_id": job_id})
    run_log.bind(job_id)  # 決策：沙盒不設 LOG_JOB_MAX_ITEMS 上限，大批量靠既有 dropped 機制截斷
    model = eff.get("model", "")
    run_log.emit(
        "stage",
        "job",
        f"Prompt 測試沙盒啟動：{len(source_ids)} 筆 × {len(prompt_ids)} prompt",
        {"model": model, "prompt_ids": prompt_ids, "scope": scope},
    )
    results: list[dict] = []
    try:
        with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as ex:
            futures = {
                ex.submit(ctx.run, _one, source, sid, prompt_ids, model): sid
                for ctx, sid in ((copy_context(), sid) for sid in source_ids)
            }
            for fut, sid in futures.items():
                try:
                    results.append(fut.result())
                except Exception as e:  # noqa: BLE001  單筆失敗不擋全批，記錯誤供回看
                    run_log.emit("error", "job", f"{sid} 測試失敗：{e}")
                    results.append({"source_id": sid, "error": str(e)})
                with _jobs_lock:
                    snap = _jobs.get(job_id)
                    if snap:
                        snap["done"] += 1
        run_log.finish(job_id)
        log_entries, _, _ = run_log.read(job_id)
        run_id = db.insert_sandbox_run(
            {
                "source": source,
                "scope": scope,
                "item_ids": source_ids,
                "prompt_ids": prompt_ids,
                "item_count": len(source_ids),
                "results": results,
                "log": log_entries,
                "model": model,
                "triggered_by": triggered_by,
                "job_id": job_id,
            }
        )
        with _jobs_lock:
            snap = _jobs.get(job_id)
            if snap:
                snap["status"] = "done"
                snap["run_id"] = run_id
    except Exception:  # noqa: BLE001  整批級失敗（如 DB 斷線）→ 標 error 供前端停輪詢
        _log.exception("Prompt 測試沙盒任務失敗 job=%s", job_id)
        with _jobs_lock:
            snap = _jobs.get(job_id)
            if snap:
                snap["status"] = "error"
    finally:
        client.set_usage_context(None)


def get_job(job_id: str) -> dict | None:
    """job 進度快照（{status: running/done/error, total, done, run_id}）；不存在回 None。"""
    with _jobs_lock:
        snap = _jobs.get(job_id)
        return dict(snap) if snap is not None else None

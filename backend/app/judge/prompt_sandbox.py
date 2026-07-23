"""歸因列表 Prompt 測試沙盒背景 job：in-mem 進度 + run_log 綁定 + 結束落庫快照。

比照 `prejudge_batch.py` 的 job registry pattern（`start` 立即回 job_id，前端輪詢 `get_job` 拿
進度），但刻意不含暫停/取消/自適應併發/計費 sink——沙盒測試是調適用途的小規模操作，不需要正式批量
初判管線的這些控制項，複製過來只會增加不必要的複雜度。

與 `prejudge_batch` 最大差異：結果不落 `attributions`/`attribution_history`（正式歸因），只落獨立的
`prompt_sandbox_runs`（見 `core/db/tables.py`），確保測試歷史與正式初判完全分離。job 結束時把
`run_log` 快照（`run_log.read`）一併存進該筆歷史，供事後回看當時的完整 LLM log（run_log 本身純
記憶體、job 淘汰後不可回溯，落庫快照是唯一持久化管道）。

版本選擇功能：可為 7 條 prompt 各自指定要用哪個歷史版本（`versions`，見
`app.judge.prompt_source.load`）。

草稿測試功能：可為 7 條 prompt 各自帶未入庫的草稿 md 全文（`drafts`，送測前逐條
`prompt_source.validate` 強驗 fail-fast）；`compare=True` 時同 job 對同批 item 雙跑——
baseline（僅 versions）與 draft（versions+drafts）各一遍，results 逐筆為
`{source_id, text, compare, baseline:{…}, draft:{…}}`（token 成本 ×2，由前端明示）；
單跑（compare=False）維持原形狀，歷史 run 渲染零遷移。草稿全文快照落 `drafts` 欄
（run 與草稿後續演進脫鉤、可溯源）。
"""

from __future__ import annotations

import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context

from app.core import db
from app.core import settings as app_settings
from app.core.job_registry import JobStore
from app.judge import prompt_eval, prompt_source, run_log
from app.judge.llm import client

_log = logging.getLogger(__name__)

# in-mem job 進度快照（單機夠用，重啟即清）；共用機制層見 core.job_registry.JobStore。
_store: JobStore = JobStore()

# 筆數間併發（每筆內部域 prompt 已用自己的 ThreadPool 並行，見 prompt_eval.domain_verdicts）；
# 沙盒測試非正式初判量級，固定小併發即可，不比照 prejudge_batch 的 per-model/自適應併發。
_MAX_WORKERS = 4


def _guard_stub(eff: dict) -> None:
    """無條件拒絕 stub 模式（不只正式環境）：Prompt 測試沙盒是「看 prompt 實際判得怎樣」的調適
    工具，stub 假結果會誤導判斷，dev 環境零 key 時也不例外（與 `prejudge_batch` 正式批量初判
    刻意放行 dev stub 的定位不同）。
    """
    if not app_settings.resolve_provider_token(eff):
        raise ValueError("目前配置無可用 LLM token（stub 模式），拒絕以假結果執行 Prompt 測試沙盒")


def start(
    source: str,
    item_ids: list[str],
    prompt_ids: list[str],
    eff: dict,
    *,
    scope: str,
    triggered_by: str = "",
    versions: dict[str, int] | None = None,
    drafts: dict[str, str] | None = None,
    compare: bool = False,
) -> str:
    """啟動沙盒測試背景 job，立即回 job_id（前端輪詢 `get_job` 拿進度）。

    Args:
        source: 來源 code（如 product_reviews）。
        item_ids: 受測 item 清單（scope=single 時長度 1）。選取筆數不設上限（使用者決策：大批量
            靠 `run_log` 既有 dropped 機制截斷 log，結果仍逐筆落庫）。
        prompt_ids: 使用者勾選的 prompt 子集（polarity / C-1..C-6）。
        eff: effective LLM 設定（呼叫端已解析，見 `app_settings.effective_llm_dict`）。
        scope: single（單列觸發）/ selection（工具列勾選多筆觸發）/ all（工具列依條件批量選取觸發）
            ——落庫供歷史列表分辨來源。
        triggered_by: 觸發人 email。
        versions: {rule_code: 指定歷史版本號}（版本選擇功能，見前端 PromptVersionPickerGroup／
            usePromptVersionPicker）。非空時逐條 fail-fast 校驗 rule_code 屬
            `prompt_source.PROMPT_RULE_CODES` 且該版本確實存在。
        drafts: {rule_code: 草稿 md 全文}（草稿測試功能）。非空時逐條 fail-fast 強驗
            （rule_code 合法 + `prompt_source.validate` 三節/Schema/佔位符/Taxonomy），
            不合法不派工——草稿存檔寬鬆、送測強驗。
        compare: 雙跑對比模式（僅 drafts 非空時有效）：每筆 item 跑 baseline（僅 versions）
            與 draft（versions+drafts）各一遍，results 逐筆為 baseline/draft 兩組。

    Returns:
        job_id（`psbxjob_` 前綴；與測試結束落庫的 `run_id` 不同——job_id 供進度輪詢/log 綁定）。

    Raises:
        ValueError: stub 模式（無可用 LLM token）——無條件拒跑，dev 亦不例外；versions 含未知
            rule_code / 不存在的版本號；或 drafts 含未知 rule_code / 驗證不過（fail-fast，不派工）。
    """
    _guard_stub(eff)
    if versions:
        for rule_code, version in versions.items():
            if rule_code not in prompt_source.PROMPT_RULE_CODES:
                raise ValueError(f"未知 rule_code：{rule_code}")
            if db.get_rule_version(rule_code, version) is None:
                raise ValueError(f"{rule_code} 無版本 {version}")
    if drafts:
        for rule_code, text in drafts.items():
            if rule_code not in prompt_source.PROMPT_RULE_CODES:
                raise ValueError(f"未知 rule_code：{rule_code}")
            prompt_id = prompt_source.prompt_id_for_rule(rule_code)
            try:
                prompt_source.validate(text, prompt_id)
            except ValueError as e:
                raise ValueError(f"{rule_code} 草稿驗證不過：{e}") from None
    compare = bool(compare and drafts)  # 無草稿時對比無意義，靜默降為單跑
    job_id = f"psbxjob_{uuid.uuid4().hex}"
    _store.put(job_id, {"status": "running", "total": len(item_ids), "done": 0, "run_id": None})
    threading.Thread(
        target=_run,
        args=(job_id, source, item_ids, prompt_ids, eff, scope, triggered_by),
        kwargs={"versions": versions, "drafts": drafts, "compare": compare},
        daemon=True,
    ).start()
    return job_id


def _one(
    source: str,
    source_id: str,
    prompt_ids: list[str],
    model: str,
    *,
    versions: dict[str, int] | None,
    drafts: dict[str, str] | None = None,
    compare: bool = False,
) -> dict:
    """單筆：組 item → `sandbox_classify`。獨立函式供 ThreadPoolExecutor 提交（copy_context 攜帶
    設定 contextvar，見 `_run`）。單筆組裝/初判失敗（如找不到該則評論）讓例外往上拋，由 `_run` 的
    `future.result()` 呼叫端接住記錄，不擋同批其他筆。

    compare=True（雙跑對比）：同一 item 跑兩遍——baseline（僅 versions，不帶草稿）與 draft
    （versions+drafts，草稿優先）——回 `{source_id, text, compare, baseline, draft}`；
    變體內不重複 source_id/text（item 級已有）。單跑維持 `sandbox_classify` 原形狀。
    """
    run_log.bind_item(source_id)  # 本筆全部 emit（含各 prompt 的 LLM 三段）自動帶 item_id
    item = prompt_eval._build_sandbox_item(source, source_id)
    if not compare:
        return prompt_eval.sandbox_classify(
            item, prompt_ids, model, versions=versions, drafts=drafts
        )
    baseline = prompt_eval.sandbox_classify(item, prompt_ids, model, versions=versions)
    draft = prompt_eval.sandbox_classify(item, prompt_ids, model, versions=versions, drafts=drafts)
    strip = ("source_id", "text")
    return {
        "source_id": baseline.get("source_id", source_id),
        "text": baseline.get("text", ""),
        "compare": True,
        "baseline": {k: v for k, v in baseline.items() if k not in strip},
        "draft": {k: v for k, v in draft.items() if k not in strip},
    }


def _run(
    job_id: str,
    source: str,
    item_ids: list[str],
    prompt_ids: list[str],
    eff: dict,
    scope: str,
    triggered_by: str,
    *,
    versions: dict[str, int] | None = None,
    drafts: dict[str, str] | None = None,
    compare: bool = False,
) -> None:
    """背景執行：bind run_log（不設筆數上限）→ 逐筆並行 `sandbox_classify`（compare 時每筆雙跑）
    → 結束落 `prompt_sandbox_runs` 快照（含 results + log 完整快照 + 草稿全文快照）。
    """
    app_settings.set_current(eff)  # 背景 thread set 好 contextvar，供 copy_context 快照攜帶
    client.set_llm_cache_read(False)  # 沙盒測試量測真實行為
    client.set_usage_context({"job_id": job_id})
    run_log.bind(job_id)  # 決策：沙盒不設 LOG_JOB_MAX_ITEMS 上限，大批量靠既有 dropped 機制截斷
    model = eff.get("model", "")

    run_log.emit(
        "stage",
        "job",
        f"Prompt 測試沙盒啟動：{len(item_ids)} 筆 × {len(prompt_ids)} prompt"
        + ("（草稿雙跑對比）" if compare else ""),
        {
            "model": model,
            "prompt_ids": prompt_ids,
            "scope": scope,
            "versions": versions or {},
            "draft_codes": sorted(drafts) if drafts else [],
            "compare": compare,
        },
    )
    results: list[dict] = []
    try:
        with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as ex:
            futures = {
                ex.submit(
                    ctx.run,
                    _one,
                    source,
                    sid,
                    prompt_ids,
                    model,
                    versions=versions,
                    drafts=drafts,
                    compare=compare,
                ): sid
                for ctx, sid in ((copy_context(), sid) for sid in item_ids)
            }
            for fut, sid in futures.items():
                try:
                    results.append(fut.result())
                except Exception as e:  # noqa: BLE001  單筆失敗不擋全批，記錯誤供回看
                    run_log.emit("error", "job", f"{sid} 測試失敗：{e}")
                    results.append({"source_id": sid, "error": str(e)})
                _store.mutate(job_id, lambda snap: snap.update({"done": snap["done"] + 1}))
        run_log.finish(job_id)
        log_entries, _, _ = run_log.read(job_id)
        run_id = db.insert_sandbox_run(
            {
                "source": source,
                "scope": scope,
                "item_ids": item_ids,
                "prompt_ids": prompt_ids,
                "item_count": len(item_ids),
                "results": results,
                "log": log_entries,
                "model": model,
                "triggered_by": triggered_by,
                "job_id": job_id,
                "versions": versions or {},
                "drafts": drafts or {},
                "compare": compare,
            }
        )
        _store.mutate(job_id, lambda snap: snap.update({"status": "done", "run_id": run_id}))
    except Exception:  # noqa: BLE001  整批級失敗（如 DB 斷線）→ 標 error 供前端停輪詢
        _log.exception("Prompt 測試沙盒任務失敗 job=%s", job_id)
        _store.set_fields(job_id, status="error")
    finally:
        client.set_usage_context(None)


def get_job(job_id: str) -> dict | None:
    """job 進度快照（{status: running/done/error, total, done, run_id}）；不存在回 None。"""
    return _store.get(job_id)


def mark_running_interrupted() -> list[str]:
    """graceful shutdown 收尾：把仍在 running 的 Prompt 測試沙盒 job 標 interrupted（語義同 export_jobs）。

    2026-07-23 補：先前遺漏此函式，shutdown.py 的彙總只涵蓋 4 套 registry，沙盒 job 在
    graceful shutdown 不會被標記——低風險（dev 調適工具、不影響正式判決），現一併補上。
    """
    return _store.mark_interrupted(running_statuses=("running",), new_status="interrupted")

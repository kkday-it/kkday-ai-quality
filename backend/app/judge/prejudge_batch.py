"""初判歸因批量編排：in-mem job registry + ThreadPool 併發初判 → 落庫 + 累計花費。

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

import logging
import threading
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextvars import copy_context

from app.core import db, pricing
from app.core import settings as app_settings
from app.core.config import env, is_production
from app.judge import prejudge, run_log
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

# 全域併發閘：多 job 疊加時把同時在跑的初判收斂到 prejudge_max_workers（見檔頭說明）。
_sem = threading.BoundedSemaphore(env.prejudge_max_workers)

# 撈 intake item 的分塊大小：避免 scope=all（~8 萬 item_id）一次塞進 IN 子句撐爆 SQL。
_FETCH_CHUNK = 500
# 失敗筆明細清單上限：大規模系統性失敗時只計數、不再細列，避免撐爆 SSE payload / 記憶體。
_MAX_FAILED_ITEMS = 200


class _ConcurrencyGovernor:
    """AIMD 自適應併發：樂觀起於 ceiling，遇 429 失敗乘性收縮、清空後加性回升，收斂到 API 可持續的最大併發。

    ceiling＝該 model 靜態上限（max_workers_for ∩ env 硬天花板），永不超過；只在其下自適應。信號＝item 因
    429 失敗（SDK 內建退避 + 單域重試全耗盡仍 429＝真過載）；SDK 能吸收的暫時 429（item 仍成功）不觸發——
    恰好在「429 開始造成失敗」時降速，零星失敗筆由 P2 重新初判補回。thread-safe（worker 併發呼叫 on_429）。
    """

    def __init__(
        self,
        ceiling: int,
        *,
        floor: int = 2,
        backoff: float = 0.5,
        probe_interval_s: float = 3.0,
        cooldown_s: float = 5.0,
    ) -> None:
        self._ceiling = max(1, ceiling)
        self._floor = max(1, min(floor, self._ceiling))
        self._backoff = backoff
        self._probe_interval = probe_interval_s
        self._cooldown = cooldown_s
        self._limit = self._ceiling  # 樂觀起步（config 值已是保守估計）
        self._last_429 = 0.0
        self._cooldown_until = 0.0
        self._lock = threading.Lock()

    def current(self) -> int:
        """當前允許併發（供提交迴圈背壓）；順帶時間驅動加性回升——僅提交執行緒呼叫（單執行緒讀）。"""
        with self._lock:
            now = time.monotonic()
            if self._limit < self._ceiling and (now - self._last_429) >= self._probe_interval:
                self._limit = min(self._ceiling, self._limit + 1)
                self._last_429 = now  # 重置探測時鐘：每 interval 回升一階（漸進不暴衝）
            return self._limit

    def on_429(self) -> None:
        """worker 遇 429 失敗時呼叫：乘性收縮（cooldown 內只反應一次，避免一波 429 過度收縮）。"""
        with self._lock:
            now = time.monotonic()
            self._last_429 = now
            if now < self._cooldown_until:
                return
            self._limit = max(self._floor, int(self._limit * self._backoff))
            self._cooldown_until = now + self._cooldown


def _is_rate_limit(exc: BaseException) -> bool:
    """例外是否為 OpenAI 429 RateLimitError（自適應併發的收縮信號）；SDK 未安裝時回 False。"""
    try:
        from openai import RateLimitError
    except Exception:  # noqa: BLE001
        return False
    return isinstance(exc, RateLimitError)


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
        # 失敗筆明細 [{item_id, source_id, error}]（上限 _MAX_FAILED_ITEMS）：供前端顯示「哪幾筆/為何失敗」
        # 與「重新初判本批失敗筆」（收 item_id 走既有 item_ids 顯式重新初判路徑）。超上限只計數並設 truncated。
        "failed_items": [],
        "failed_items_truncated": False,
    }


def _bump(
    job_id: str,
    *,
    ok: bool,
    tokens: int = 0,
    cost: float = 0.0,
    item_id: str = "",
    source_id: str = "",
    error: str = "",
) -> None:
    """單筆初判完成後累加進度（thread-safe；processed / ok|failed；失敗附 item 明細供前端清單）。"""
    with _jobs_lock:
        snap = _jobs.get(job_id)
        if snap is None:
            return
        snap["processed"] += 1
        snap["ok" if ok else "failed"] += 1
        if not ok:
            fi = snap["failed_items"]
            if len(fi) < _MAX_FAILED_ITEMS:
                fi.append({"item_id": item_id, "source_id": source_id, "error": error})
            else:
                snap["failed_items_truncated"] = True


def _work_one(
    job_id: str,
    item: dict,
    model: str,
    source: str | None,
    triggered_by: str = "",
    governor: _ConcurrencyGovernor | None = None,
    prompt_versions: dict[str, int] | None = None,
    versions_used: dict[str, int] | None = None,
) -> None:
    """初判單筆 → 落庫；例外計 failed 不中斷整批（全域 Semaphore 收斂併發）。

    item 為來源表列（源欄名）。先注入 canonical content + source_id + source（供 prejudge 引擎），
    全 5 來源統一走 to_findings（1:N 多歸因），以 replace_source_findings 整組替換 (source, source_id)
    舊列（重新初判冪等、保留人工判決 status）。

    prompt_versions：使用者指定的版本覆蓋（{rule_code: version}，可為 None/部分），僅傳入未被指定的
    prompt 仍走 DB active（維持既有 `_cache` 快取路徑，不因版本選擇功能拖累整批效能）。
    versions_used：本次 job 完整 7 條 prompt 版本快照（`_run` 算好傳入，含未指定、沿用 active 的），
    純供稽核落庫（`attribution_history.params`），不影響初判本身用哪個版本。
    """
    from app.core import source_mapping as _srcmap
    from app.core.db import attribution_history
    from app.core.db import source_registry as _reg

    with _sem:
        source_id = ""  # 於 try 內更新；先置空供 except 分支安全引用（早期失敗時 source_id 未算出）
        try:
            src = source or ""
            spec = _reg.spec_for(src)
            canon = _srcmap.normalize_row(src, item) if src in _srcmap.sources() else {}
            source_id = str(item.get(spec.natural_key) or "") if spec else ""
            # per-item 用量情境（worker 自身 copied context，隔離）：附 source_id 供 llm_usage 落庫歸戶
            client.set_usage_context({"job_id": job_id, "source": src, "source_id": source_id})
            # 日誌 item 歸屬蓋章：本筆全部 emit（含六域 ThreadPool 內的 LLM 三段）自動帶 item_id
            run_log.bind_item(source_id or str(item.get("item_id", "")))
            norm = dict(item)
            norm["source"] = src
            norm["source_id"] = source_id
            norm["content"] = canon.get("content") or ""  # 初判主輸入（各來源源欄→canonical）
            norm["title"] = canon.get("title") or ""  # 標題（rec_title/subject；_text_of 前置一行）
            norm["prod_oid"] = canon.get("prod_oid") or ""
            norm["order_oid"] = canon.get("order_oid") or ""
            norm["raw"] = item  # 供 _evidence_cap 讀 order_oid
            run_log.emit(
                "stage",
                "item",
                f"開始初判 {source_id or item.get('item_id', '')}",
                {
                    "source": src,
                    "source_id": source_id,
                    "title": norm["title"],
                    "content": (norm["content"] or "")[:400],
                },
            )
            findings = prejudge.to_findings(norm, model=model, versions=prompt_versions)
            # 完成日誌附歸因結果 digest（傾向/L1›L2/信心/摘要/未匹配理由），流程 tab 一目瞭然免切詳情
            digest = [
                {
                    "polarity": f.polarity,
                    "l1": f.l1_label or f.l1_domain_code,
                    "l2": f.l2_label or f.l2_code,
                    "confidence": round(f.confidence, 2),
                    "tier": f.confidence_tier,
                    "summary": (f.summary or {}).get("zh-tw")
                    or next(iter((f.summary or {}).values()), ""),
                }
                for f in findings
            ]
            run_log.emit(
                "stage",
                "item",
                f"歸類完成 {source_id}：{len(findings)} 筆歸因",
                {"findings": digest},
            )
            # 初判參數精餾快照（評論級歷史去重比對鍵之一；勿塞 job 級大清單）
            history_params = {"model": model}
            if versions_used:
                history_params["prompt_versions"] = versions_used
            db.replace_source_findings(
                src,
                source_id,
                findings,
                params=history_params,
                job_id=job_id,
                triggered_by=triggered_by,
            )
            run_log.emit("stage", "db", f"落庫完成 {source_id}")
            _bump(job_id, ok=True)
        except Exception as e:  # noqa: BLE001  單筆失敗隔離，不讓一筆炸掉整批
            item_id = str(item.get("item_id") or "")
            err = str(e).splitlines()[0][:200] if str(e).strip() else type(e).__name__
            run_log.emit("error", "item", f"單筆初判失敗 {source_id or item_id}：{err}")
            _log.exception("初判歸因單筆失敗 job=%s item=%s", job_id, item_id)
            _bump(job_id, ok=False, item_id=item_id, source_id=source_id, error=err)
            if governor is not None and _is_rate_limit(e):
                governor.on_429()  # 429 造成的失敗＝真過載 → 自適應收縮併發（下波提交降速）
            # 失敗留痕（best-effort）：有 source_id（可歸戶）才寫，供前端查因 + 隱式重撈上限
            if source_id:
                attribution_history.insert_failure_event(
                    source or "", source_id, error=err, job_id=job_id, triggered_by=triggered_by
                )


def _reload_judge_rules() -> None:
    """批次初判啟動前強制 reload 各判準 loader（ai_judge 分類結構 / judgment 極性閘門+證據政策+旋鈕 /
    flags 閾值 / prompt_source），保證本批每筆初判都採用『當前 DB active 版規則』。

    根因：初判 server 把規則快取在 process 記憶體，規則經 UI 存檔雖由 rules._reload_judge_cache 熱重載
    「該台 server」，但 out-of-band 改動（腳本 / migration / 別台 server 發布）不會通知本 process → 快取
    stale → LLM 判到舊規則。批次入口再 reload 一次即成硬保證，與『每次初判用最新規則』的預期一致。
    """
    from app.core import ai_judge, flags
    from app.core.db import _shared
    from app.judge import prejudge

    for fn in (
        ai_judge.reload,  # 連動清 prompt_source md 快取（見 ai_judge.reload docstring）
        _shared.reload_pipeline_cfg,
        prejudge.reload,
        flags.reload,
    ):
        try:
            fn()
        except Exception:  # noqa: BLE001  單一 loader reload 失敗不阻斷整批初判
            pass


def _resolve_versions_used(pinned: dict[str, int] | None) -> dict[str, int]:
    """本次 job 完整 7 條 prompt 版本快照（稽核用）：使用者指定的用指定值，沒指定的補當下 active
    版本號。job 級算一次（非逐筆），寫入 `attribution_history.params.prompt_versions`——沒有這個快照，
    「這筆初判當初到底用哪個版本判的」在未來 active 版被覆寫後就永久無法回答。
    """
    from app.judge import prompt_source

    active = {
        m["rule_code"]: m["version"]
        for m in db.list_rule_meta()
        if m["rule_code"] in prompt_source.PROMPT_RULE_CODES
    }
    return {**active, **(pinned or {})}


def _run(
    job_id: str,
    item_ids: list[str],
    eff: dict,
    model: str,
    source: str | None = None,
    cache_read: bool = True,
    triggered_by: str = "",
    prompt_versions: dict[str, int] | None = None,
) -> None:
    """背景執行整批初判：注入設定 contextvar → 分塊撈 item → 有背壓地逐筆提交（支援暫停/取消）→ 標記結束。"""
    # 正式環境 stub 第二道防線（主閘在 judgment router）：擋繞過 API 直呼 start_job 的路徑
    # （腳本/排程誤用）與 eff 中途被清空的極端情況——假判會靜默覆蓋真實歸因，寧錯殺不放行。
    if is_production() and not app_settings.resolve_provider_token(eff):
        _log.error("job=%s 正式環境偵測不到有效 LLM token，拒絕以 stub 執行（第二道防線）", job_id)
        _set_status(job_id, "error")
        return
    _reload_judge_rules()  # 硬保證：本批每筆 LLM 初判都採用『當前 DB active 版規則』（防 server 記憶體舊快取）
    # 批次 serving tier（prejudge.json/verdict.json prejudge.batch_service_tier；flex＝-50% 換延遲，小批不套）：
    # 注入 eff 後由 client 依 provider 守門送出；429 資源不足 client 自動回退標準 tier。
    tier = prejudge.batch_service_tier(len(item_ids))
    if tier:
        eff = {**eff, "service_tier": tier}
    # 批次 reasoning_effort 硬上限（prejudge.json/verdict.json prejudge.batch_max_reasoning_effort）：
    # active LLM 檔位若設 xhigh（診斷用），全量批次誤用會讓 reasoning token 暴增 ~6x、費用近 10x
    # ——制度性防呆壓檔；單筆/沙盒呼叫不經此路徑，不受影響。
    capped_effort = prejudge.cap_batch_reasoning_effort(eff.get("reasoning_effort"))
    if capped_effort != eff.get("reasoning_effort"):
        _log.warning(
            "job=%s reasoning_effort=%s 超出批次上限，壓至 %s（batch_max_reasoning_effort）",
            job_id,
            eff.get("reasoning_effort"),
            capped_effort,
        )
        eff = {**eff, "reasoning_effort": capped_effort}
    # 在背景 thread 的 context 內 set 好 contextvar，稍後每筆任務 copy_context 快照即帶上。
    app_settings.set_current(eff)
    # P1b flex 回退量測：job 始末取全域計數差值（多 job 併發時含他 job 流量，量測全域占比可接受）
    flex_before = client.flex_stats()
    # LLM exact-cache 讀取閘：批次開（重用規則未變部分·零 token）；顯式重新初判關（使用者要求真的重打）
    client.set_llm_cache_read(cache_read)
    # 小批量 job 收集執行日誌（前端抽屜 SSE 即時檢視）；bind 後 copy_context 快照自動攜帶歸屬
    if len(item_ids) <= run_log.LOG_JOB_MAX_ITEMS:
        run_log.bind(job_id)
        run_log.emit(
            "stage",
            "job",
            f"初判任務啟動：{len(item_ids)} 筆",
            {
                "model": model,
                "base_url": eff.get("base_url"),
                "temperature": eff.get("temperature"),
                "thinking": eff.get("thinking"),
                "reasoning_effort": eff.get("reasoning_effort"),
                "service_tier": tier,
                "cache_read": cache_read,
            },
        )

    def _sink(m: str, prompt: int, completion: int, cached: int = 0) -> None:
        """token 用量回報：累計 total_tokens 並依模型單價加總 cost_usd（cached 折扣＋job 級 tier 折扣）。

        tier 取 job 級設定（flex 個別呼叫 429 回退標準時此處會微幅低估；權威 per-call 計價在
        llm_usage 落庫，帶實際生效 tier）。thread-safe。
        """
        with _jobs_lock:
            snap = _jobs.get(job_id)
            if snap is None:
                return
            snap["total_tokens"] += prompt + completion
            snap["cost_usd"] = round(
                snap["cost_usd"]
                + pricing.cost_usd(m, prompt, completion, cached, service_tier=tier),
                6,
            )

    # 版本選擇功能：job 級算一次完整快照（稽核用），逐筆只轉發使用者實際指定的 pinned 子集
    # （未指定的 prompt 仍走 to_findings 的 DB active 快取路徑，見 _work_one 說明）。
    versions_used = _resolve_versions_used(prompt_versions)
    client.set_usage_sink(_sink)
    # per-call 用量落庫：base 情境（job/source）+ 共用 buffer（copy_context 前設定→worker 共用同一 list），
    # 各 worker 於 _work_one 覆寫 source_id；job 結束 flush bulk insert 進 llm_usage。
    client.set_usage_context({"job_id": job_id, "source": source or ""})
    usage_buf = client.open_usage_buffer()
    ctrl = _controls.get(job_id, {})
    gate, cancel = ctrl.get("gate"), ctrl.get("cancel")
    # 併發上限：依生效 model 的軟上限（prejudge.json/verdict.json prejudge.max_workers_by_model）與製程級硬天花板
    # env.prejudge_max_workers 取 min——per-model 只能往下收斂，不會超過全域 _sem 容量造成隱性排隊。
    max_workers = min(prejudge.max_workers_for(model), env.prejudge_max_workers)
    # 自適應併發（AIMD）：max_workers 作 ceiling，governor 在其下依 429 失敗自動收縮/回升（保證有能力
    # 時爬回 ceiling、過載時才降）；關閉則固定 max_workers。
    _ac = prejudge.adaptive_concurrency()
    governor = (
        _ConcurrencyGovernor(
            max_workers,
            floor=_ac["floor"],
            backoff=_ac["backoff"],
            probe_interval_s=_ac["probe_interval_s"],
        )
        if _ac["enabled"]
        else None
    )
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
                    while len(in_flight) >= (governor.current() if governor else max_workers):
                        _, in_flight = wait(in_flight, return_when=FIRST_COMPLETED)
                    c = copy_context()  # 每筆獨立快照（同一 Context 不可並發 run）
                    in_flight.add(
                        ex.submit(
                            c.run,
                            _work_one,
                            job_id,
                            item,
                            model,
                            source,
                            triggered_by,
                            governor,
                            prompt_versions,
                            versions_used,
                        )
                    )
                if cancel and cancel.is_set():
                    break
            wait(
                in_flight
            )  # drain 剩餘（正常跑完 / 取消後已提交的收斂；with 結束亦 shutdown(wait=True)）
        _set_status(job_id, "cancelled" if (cancel and cancel.is_set()) else "done")
    except Exception:  # noqa: BLE001  整批級失敗（如 DB 連線斷）→ 標 error 供前端停輪詢
        _log.exception("初判歸因批量任務失敗 job=%s", job_id)
        _set_status(job_id, "error")
    finally:
        client.set_usage_sink(None)
        try:  # flush 本 job 累積的 per-call 用量列（best-effort，計費不阻斷）
            db.insert_llm_usage_rows(usage_buf)
        except Exception:  # noqa: BLE001
            _log.debug("llm_usage flush 失敗 job=%s", job_id)
        # P1b flex 回退量測：log 差值（fallbacks/attempts 即漏折扣占比；>5% 依計畫立項 Batch API lane）
        fs = client.flex_stats()
        att = fs["attempts"] - flex_before["attempts"]
        fb = fs["fallbacks"] - flex_before["fallbacks"]
        if att > 0:
            _log.info(
                "job=%s flex 統計 attempts=%d fallbacks=%d（回退率 %.1f%%）",
                job_id,
                att,
                fb,
                100.0 * fb / att,
            )
        client.set_usage_context(None)
        try:  # 歸因歷史終態回寫（於 llm_usage flush 後，詳情頁 per-stage 明細即刻可查）
            snap = get_job(job_id)
            if snap:
                db.finish_prejudge_run(job_id, snap)
        except Exception:  # noqa: BLE001
            _log.exception("歸因歷史終態回寫失敗 job=%s", job_id)
        run_log.finish(job_id)  # 日誌收尾（未 bind 的大批量 job 為 no-op）；SSE 讀盡即關閉
        try:  # 落存執行日誌快照（僅小批量 job 有收集內容）供歸因歷史「查看 LLM 日誌」事後回看
            entries, _done, exists = run_log.read(job_id)
            if exists:
                db.save_run_log(job_id, entries)
        except Exception:  # noqa: BLE001
            _log.exception("執行日誌落庫失敗 job=%s", job_id)
        _drop_controls(job_id)


def _set_status(job_id: str, status: str) -> None:
    """設定 job 狀態（thread-safe）；同步回寫歸因歷史（best-effort，不阻斷初判）。"""
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id]["status"] = status
    try:  # 非終態（paused/running/cancelling）即時回寫；終態統計另由 _run finally 的 finish 覆蓋
        db.update_prejudge_run_status(job_id, status)
    except Exception:  # noqa: BLE001
        _log.debug("歸因歷史狀態回寫失敗 job=%s status=%s", job_id, status)


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
    try:  # 歸因歷史狀態同步（best-effort）
        db.update_prejudge_run_status(job_id, "paused")
    except Exception:  # noqa: BLE001
        pass
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
    try:  # 歸因歷史狀態同步（best-effort）
        db.update_prejudge_run_status(job_id, "running")
    except Exception:  # noqa: BLE001
        pass
    return True


def cancel_job(job_id: str) -> bool:
    """停止 job：set cancel + gate（喚醒暫停中迴圈）；status→cancelling（drain 後由 _run 轉 cancelled）。

    回 True＝成功（job 存在且未達終態）。已初判 finding 已落庫保留；剩餘未初判可事後重跑。
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
    try:  # 歸因歷史狀態同步（best-effort；drain 完由 _run 終態 finish 覆蓋為 cancelled）
        db.update_prejudge_run_status(job_id, "cancelling")
    except Exception:  # noqa: BLE001
        pass
    return True


def start_job(
    item_ids: list[str],
    eff: dict,
    model: str,
    source: str | None = None,
    *,
    triggered_by: str = "",
    kind: str = "batch",
    rejudge: bool = False,
    params: dict | None = None,
    cache_read: bool = True,
    prompt_versions: dict[str, int] | None = None,
) -> str:
    """註冊並背景啟動一個初判歸因批量任務；立即回 job_id（不阻塞請求）。

    Args:
        item_ids: 初判標的 item_id 清單（端點已解析：顯式選取 / scope=all 未初判集合）。
        eff: effective LLM dict（settings.effective_llm_dict 產；含 model/token/reasoning）。
        model: 主初判模型名（Stage2/2b；stub 模式引擎自走啟發式）。
        source: 來源 code（穿透至 get_items_by_ids 選表 + insert_finding 記錄來源；
            None＝沿用 intake_items 舊行為）。
        triggered_by: 觸發人（user email；歸因歷史落庫）。
        kind: 觸發型態（batch/selected/single；歸因歷史落庫，端點解析）。
        rejudge: 標的先前已有初判（本次為重新初判；端點判定）。
        params: 發起參數快照（歸因歷史落庫供追溯；勿含大清單）。
        cache_read: LLM exact-cache 讀取閘（批次 True＝重用規則未變部分；顯式單筆/選取重新初判 False＝真的重打。寫入恆開）。
        prompt_versions: 使用者指定的 prompt 版本覆蓋（{rule_code: version}；版本選擇功能，正式初判
            不支援草稿，僅支援指定歷史版本——見 app.judge.prompt_source.load 的 versions 參數）。

    Returns:
        job_id（前端據此輪詢 get_job）。
    """
    job_id = f"pj_{uuid.uuid4().hex[:12]}"
    with _jobs_lock:
        _jobs[job_id] = _new_snapshot(len(item_ids), model)
        gate = threading.Event()
        gate.set()  # 預設可跑（暫停時清除）
        _controls[job_id] = {"gate": gate, "cancel": threading.Event()}
    try:  # 歸因歷史建檔（run 級持久化；失敗不阻斷初判——歷史為輔助紀錄）
        db.insert_prejudge_run(
            {
                "job_id": job_id,
                "kind": kind,
                "rejudge": rejudge,
                "source": source or "",
                "model": model,
                "params": params or {},
                "status": "running",
                "total": len(item_ids),
                "triggered_by": triggered_by,
            }
        )
    except Exception:  # noqa: BLE001
        _log.exception("歸因歷史建檔失敗 job=%s", job_id)
    threading.Thread(
        target=_run,
        args=(job_id, item_ids, eff, model, source, cache_read, triggered_by, prompt_versions),
        name=f"prejudge-{job_id}",
        daemon=True,
    ).start()
    return job_id


def get_job(job_id: str) -> dict | None:
    """取 job 進度快照複本（thread-safe）；不存在回 None（端點轉 404）。"""
    with _jobs_lock:
        snap = _jobs.get(job_id)
        return dict(snap) if snap else None

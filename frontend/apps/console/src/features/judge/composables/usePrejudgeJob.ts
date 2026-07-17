// 初判歸因批次任務：跑批 + SSE 進度 + 暫停/恢復/停止 + 目標選取彈窗 + 單列重新初判。
// 自 useAttributionList 下沉；依賴（來源 / 選中模型 / 垂直分類 / 勾選列 / 重載回呼）由呼叫端注入，
// 回傳之 ref 保留原 identity（呼叫端 spread 進 return，模板綁定不變）。
import {
  computed,
  onScopeDispose,
  reactive,
  ref,
  toValue,
  type ComputedRef,
  type MaybeRefOrGetter,
  type Ref,
} from 'vue';
import { Message } from '@arco-design/web-vue';
import prejudgeCfg from '@config/ai_judge/prejudge.json';
import {
  cancelPrejudge,
  pausePrejudge,
  prejudgeLogStreamUrl,
  prejudgeStreamUrl,
  previewPrejudgeCount,
  resumePrejudge,
  startPrejudge,
  type PrejudgeBody,
  type PrejudgeFailedItem,
} from '@/api';
import { emptyFilters, filtersToParams, STAGE_LABELS } from '../constants';
import type { LogEntry } from '../components/PrejudgeLogView.types';

/** 頁面列表篩選快照（開彈窗時自動帶入彈窗內目標篩選草稿的初值；鍵名對齊 getProblems 參數）。 */
export interface PrejudgeListFilters {
  /** 傾向（頁面傾向多選篩選；開彈窗時取第一項作為再判收斂傾向的預設值）。 */
  polarity?: string[];
  /** 信心分層（初判級，僅已初判分支）。 */
  confidenceTier?: string;
  /** 歸因分類（初判級，僅已初判分支；多選任意層級 code，子樹語義）。 */
  taxonomy?: string[];
  /** 有無外部評論（表級，兩分支皆套）：''／undefined＝全部、'true'＝有、'false'＝無。 */
  hasExternal?: string;
  dateFrom?: string;
  dateTo?: string;
  recOid?: string;
  prodOid?: string;
  orderOid?: string;
}

/** usePrejudgeJob 的注入依賴。 */
interface PrejudgeJobDeps {
  /** 目前選定來源（getter / ref / 純值）。 */
  source: MaybeRefOrGetter<string>;
  /** 選中的已保存 LLM 配置 id（來自 useLlmConfigs）。 */
  llmConfigId: Ref<string>;
  /** 生效的商品垂直分類（送查詢用；全選/未選為 undefined）。 */
  effVerticals: ComputedRef<string[] | undefined>;
  /** 跨頁累積的勾選 review（source_id）；selected 模式目標與 item_ids 用。 */
  selectedKeys: Ref<string[]>;
  /** 頁面當前列表篩選快照（scope 模式套用；undefined 值代表該維度未設）。 */
  listFilters: ComputedRef<PrejudgeListFilters>;
  /** 初判完成後重載列表 + 未初判數（就地看到結果）。 */
  reload: () => Promise<void>;
}

/**
 * 初判歸因批次任務控制。
 * @returns 進度狀態、目標選取、批次動作（跑/暫停/恢復/停止）、單列重新初判 loading + 觸發。
 */
export function usePrejudgeJob(deps: PrejudgeJobDeps) {
  const { source, llmConfigId, effVerticals, selectedKeys, listFilters, reload } = deps;

  const running = ref(false);
  /** 當前 job_id（供暫停/恢復/停止；無執行中為空）。 */
  const jobId = ref('');
  /** 當前 job 狀態（running/paused/cancelling/cancelled/done/error；由 SSE 權威更新，動作先樂觀設）。 */
  const jobStatus = ref('');
  const progress = ref({ processed: 0, total: 0, totalTokens: 0, costUsd: 0 });
  /** 本批失敗筆明細（SSE snapshot.failed_items；供顯示原因 + 「重新初判本批失敗筆」收 item_id）。 */
  const failedItems = ref<PrejudgeFailedItem[]>([]);
  /** 失敗筆超過後端上限、清單已截斷（只計數；重新初判仍以清單內可見者為準）。 */
  const failedTruncated = ref(false);
  const progressPct = computed(() =>
    progress.value.total ? Math.round((progress.value.processed / progress.value.total) * 100) : 0,
  );
  /** token 花費顯示（金額 4 位小數，token 千分位）；批量初判過程同步更新。 */
  const costText = computed(() =>
    progress.value.totalTokens
      ? `${progress.value.totalTokens.toLocaleString()} tokens · ≈ $${progress.value.costUsd.toFixed(4)}`
      : '',
  );
  // 以 SSE 長連線接收初判進度（取代 setInterval 輪詢）；到達終態（done/error/cancelled）即
  // resolve。批量與單列共用同一份 jobStatus/progress——兩者以雙向互斥保證同時只有一個 job 在跑
  // （見 _run 與 rejudgeRow 開頭的 guard），故共寫安全。互斥的釋放時機＝_poll resolve，因此
  // error 處理不可把「原生瞬斷」誤判成 job 結束（會提前放鎖＋假完成訊息，而後端 job 仍在跑）：
  // 瞬斷交給 EventSource 自動重連，僅「帶 data 的明確終止 event / 連線已 CLOSED / 連續多次
  // 重連失敗（如後端重啟遺失 job）」才放手。
  const _poll = (jid: string) =>
    new Promise<void>((resolve) => {
      const es = new EventSource(prejudgeStreamUrl(jid));
      let errStreak = 0;
      const finish = () => {
        es.close();
        resolve();
      };
      es.onopen = () => {
        errStreak = 0;
      };
      es.onmessage = (ev) => {
        errStreak = 0;
        const st = JSON.parse(ev.data);
        jobStatus.value = st.status || jobStatus.value; // SSE 權威狀態（涵蓋 paused/cancelling）
        progress.value = {
          processed: st.processed || 0,
          total: st.total || progress.value.total,
          totalTokens: st.total_tokens || 0,
          costUsd: st.cost_usd || 0,
        };
        if (Array.isArray(st.failed_items)) failedItems.value = st.failed_items;
        failedTruncated.value = !!st.failed_items_truncated;
        if (st.status === 'done' || st.status === 'error' || st.status === 'cancelled') finish();
      };
      es.addEventListener('error', (ev) => {
        errStreak += 1;
        if ((ev as MessageEvent).data || es.readyState === EventSource.CLOSED || errStreak >= 5)
          finish();
      });
    });

  // ── 確認初判分類抽屜內嵌執行日誌（批量／單列共用）：抽屜不再另開獨立的 PrejudgeLogDrawer，
  // 抽屜本身收合設定面板後直接顯示這份即時 log，SSE 生命週期比照 PromptSandboxDrawer._openLogStream。
  const logEntries = ref<LogEntry[]>([]);
  const logStreaming = ref(false);
  /** 後端明確終止日誌流的原因（如大批量任務不收集日誌）；空＝無錯誤。 */
  const logError = ref('');
  let _logEs: EventSource | null = null;
  const _closeLog = () => {
    _logEs?.close();
    _logEs = null;
    logStreaming.value = false;
  };
  const _openLog = (jid: string) => {
    _closeLog();
    logEntries.value = [];
    logError.value = '';
    logStreaming.value = true;
    _logEs = new EventSource(prejudgeLogStreamUrl(jid));
    _logEs.onopen = () => {
      logEntries.value = []; // 自動重連會整批重放 → 先清空避免重複
    };
    _logEs.onmessage = (ev) => logEntries.value.push(JSON.parse(ev.data));
    _logEs.addEventListener('done', () => _closeLog());
    _logEs.addEventListener('error', (ev) => {
      // 僅後端明確推送的 error event（帶 data，如「此任務無日誌」）才終止；原生連線瞬斷無
      // data → 不關閉，交給 EventSource 自動重連（首連瞬斷就永久關流會讓日誌看起來「沒反應」）。
      const data = (ev as MessageEvent).data;
      if (data) {
        try {
          logError.value = JSON.parse(data).detail || '日誌串流失敗';
        } catch {
          logError.value = '日誌串流失敗';
        }
        _closeLog();
      }
    });
  };
  // 頁面（消費端元件）卸載時關閉殘留的 log 串流，避免 EventSource 洩漏（_poll 的進度流
  // 生命週期綁 job 終態自關，不需在此處理）。
  onScopeDispose(_closeLog);

  const _run = async (body: PrejudgeBody) => {
    if (running.value) return;
    if (rowBusy.value.size) {
      // 與 rejudgeRow 的 running guard 對稱：批量與單列共用 jobStatus/progress/log 流，
      // 併發會互相覆寫顯示（且可能對同一 finding 重複初判）
      Message.warning('單列初判進行中，請稍後再試');
      return;
    }
    running.value = true;
    jobStatus.value = 'running';
    progress.value = { processed: 0, total: 0, totalTokens: 0, costUsd: 0 };
    failedItems.value = []; // 新批次重置（保留上一批失敗清單至下次開跑，供期間點「重新初判失敗筆」）
    failedTruncated.value = false;
    try {
      const r = await startPrejudge({ ...body, llm_config_id: llmConfigId.value || undefined });
      jobId.value = r.job_id;
      progress.value = { processed: 0, total: r.total, totalTokens: 0, costUsd: 0 };
      if (!r.total) {
        Message.warning('沒有可分析的對象');
        return;
      }
      _openLog(r.job_id);
      await _poll(r.job_id);
      if (jobStatus.value === 'cancelled') {
        Message.info(
          `已停止：已處理 ${progress.value.processed}/${progress.value.total} 筆（已初判結果保留）`,
        );
      } else if (jobStatus.value === 'error') {
        Message.error('初判分類任務失敗（詳見執行日誌）');
      } else if (jobStatus.value === 'done') {
        Message.success(`初判分類完成：${progress.value.processed} 筆（模型 ${r.model}）`);
      } else {
        // _poll 因連線持續失敗放手（非終態）：後端 job 可能仍在跑，勿假報成功
        Message.warning('進度連線中斷：任務可能仍在背景執行，稍後重新整理列表確認結果');
      }
      await reload(); // 重載當前頁（保持頁碼，就地看到結果）
    } catch (e: any) {
      Message.error('初判分類失敗：' + (e?.message || e));
    } finally {
      running.value = false;
      jobId.value = '';
      jobStatus.value = '';
      _closeLog();
    }
  };
  /** 暫停當前 job（樂觀設 paused，SSE 隨後權威更新）。 */
  const pauseJob = async () => {
    if (!jobId.value) return;
    try {
      await pausePrejudge(jobId.value);
      jobStatus.value = 'paused';
    } catch (e: any) {
      Message.error('暫停失敗：' + (e?.message || e));
    }
  };
  /** 恢復當前已暫停的 job。 */
  const resumeJob = async () => {
    if (!jobId.value) return;
    try {
      await resumePrejudge(jobId.value);
      jobStatus.value = 'running';
    } catch (e: any) {
      Message.error('恢復失敗：' + (e?.message || e));
    }
  };
  /** 停止當前 job（不再派新工，已在跑的收斂後轉 cancelled；已初判已落庫）。 */
  const cancelJob = async () => {
    if (!jobId.value) return;
    try {
      await cancelPrejudge(jobId.value);
      jobStatus.value = 'cancelling';
    } catch (e: any) {
      Message.error('停止失敗：' + (e?.message || e));
    }
  };
  // 初判歸因統一彈窗 + 目標選取（stage 驅動）：於彈窗選範圍/收斂/model 再確認執行
  const confirmOpen = ref(false);
  /** 選取範圍：selected＝在「已選 N 筆」內做階段+篩選目標選取（within_ids 交集）；scope＝全部資料。 */
  const targetMode = ref<'selected' | 'scope'>('scope');
  const targetStages = ref<string[]>(['unjudged']); // 預設只收未初判
  const lowConfOnly = ref(true); // true＝僅低信心(<auto_accept)；false＝全部信心
  const targetCount = ref(0); // 「將處理 N 筆」預覽
  // ── 彈窗內目標篩選草稿：開彈窗自動帶入頁面當前列表篩選，彈窗內可下拉重選，只影響本次初判目標 ──
  // 與列表同形狀（AttributionFilters）→ 彈窗 AttributionFilterBar 直接綁定（統一完整篩選欄）。
  // 表級（dateRange/oid/hasExternal）兩分支皆套；初判級（polarity/tier/taxonomy）只對已初判分支送
  // （未初判列無初判）。stage 由上方 checkbox 承擔 → 此欄不納入彈窗篩選。
  const draftFilters = reactive(emptyFilters());
  /** 是否含已初判階段（非 unjudged）→ 顯示傾向/信心收斂條件。 */
  const hasJudgedStage = computed(() => targetStages.value.some((s) => s !== 'unjudged'));

  /** 彈窗草稿 → 篩選快照（空值一律轉 undefined；鍵名對齊 getProblems / 後端參數）。 */
  const _lf = (): PrejudgeListFilters => {
    const p = filtersToParams(draftFilters);
    return {
      polarity: p.polarity,
      confidenceTier: p.confidenceTier,
      taxonomy: p.taxonomy,
      hasExternal: p.hasExternal,
      dateFrom: p.dateFrom,
      dateTo: p.dateTo,
      recOid: p.recOid,
      prodOid: p.prodOid,
      orderOid: p.orderOid,
    };
  };
  /** 組 scope 目標選取請求 body（doRun 與筆數預覽共用同一形狀 → 預覽=實跑）：
   *  stage 驅動 + 目標篩選草稿全維度；範圍=已選時再以 within_ids 交集勾選列。 */
  const _scopeBody = (): PrejudgeBody => {
    const lf = _lf();
    return {
      source: toValue(source),
      scope: 'all',
      product_verticals: effVerticals.value,
      stages: targetStages.value,
      within_ids: targetMode.value === 'selected' ? [...selectedKeys.value] : undefined,
      date_from: lf.dateFrom,
      date_to: lf.dateTo,
      rec_oid: lf.recOid,
      prod_oid: lf.prodOid,
      order_oid: lf.orderOid,
      // 有無外部評論為表級（兩分支皆套）→ 恆送；字串轉 boolean（對齊 useAttributionList 的送法）
      has_external: lf.hasExternal === undefined ? undefined : lf.hasExternal === 'true',
      ...(hasJudgedStage.value
        ? {
            target_polarity: lf.polarity,
            max_confidence: lowConfOnly.value
              ? prejudgeCfg.confidence_tiers.auto_accept
              : undefined,
            confidence_tier: lf.confidenceTier,
            taxonomy: lf.taxonomy,
          }
        : {}),
    };
  };

  // 單調遞增請求序號：快速切換條件會併發多次 refresh，僅最後一次可寫入 targetCount（防慢回應覆蓋新值）。
  let countSeq = 0;
  /** 「將處理 N 筆」預覽：與 doRun 同一 body 打後端 count 端點（同一套標的解析，含 max_confidence 精算）。 */
  const refreshTargetCount = async () => {
    const seq = ++countSeq;
    try {
      const r = await previewPrejudgeCount(_scopeBody());
      if (seq === countSeq) targetCount.value = r.total; // 過期回應（已有更新的 refresh）丟棄，不覆蓋
    } catch {
      /* 預覽失敗維持上次值不阻斷操作；實跑筆數仍以 startPrejudge 後端解析為準 */
    }
  };

  /** 開初判歸因彈窗：目標篩選草稿自動帶入頁面當前列表篩選（彈窗內可重選）。
   *  範圍預設：有勾選＝「已選內」且收全部階段、不收斂（初始目標＝整個勾選集合，對齊「判我勾的」直覺）；
   *  無勾選＝全部資料且只判未初判、再判收斂預設負向+僅低信心（安全預設，避免誤重新初判全庫）。 */
  const openPrejudge = () => {
    const hasSel = selectedKeys.value.length > 0;
    targetMode.value = hasSel ? 'selected' : 'scope';
    targetStages.value = hasSel ? Object.keys(STAGE_LABELS) : ['unjudged'];
    const lf = listFilters.value;
    draftFilters.dateRange = lf.dateFrom && lf.dateTo ? [lf.dateFrom, lf.dateTo] : [];
    draftFilters.recOid = lf.recOid || '';
    draftFilters.prodOid = lf.prodOid || '';
    draftFilters.orderOid = lf.orderOid || '';
    draftFilters.hasExternal = lf.hasExternal || '';
    draftFilters.tier = lf.confidenceTier || '';
    draftFilters.taxonomy = lf.taxonomy ? [...lf.taxonomy] : [];
    // 傾向：帶入列表當前傾向；無選取的預設 scope（只判未初判）用 negative 兜底——
    // 避免使用者臨時加勾已初判階段卻沒選傾向時，把整庫已初判全數重新初判。
    draftFilters.polarity = hasSel ? [] : lf.polarity?.length ? [...lf.polarity] : ['negative'];
    lowConfOnly.value = !hasSel;
    confirmOpen.value = true;
    void refreshTargetCount();
  };

  /** 二次確認後執行：範圍（全部/已選內）+ 階段 + 篩選草稿統一走 scope 目標選取（與預覽同 body）。
   * @param promptVersions 版本選擇功能：指定的 {rule_code: 版本號}（未指定的沿用 active，見
   *   PromptVersionPickerGroup／usePromptVersionPicker，正式初判不支援草稿只支援指定版本）。 */
  const doRun = (promptVersions?: Record<string, number>) => {
    // 抽屜不再於送出時自動關閉——確認後直接在原抽屜切換顯示執行日誌（見 logEntries/logStreaming），
    // 讓使用者可留在原地看即時 log；關閉交由使用者自己按「關閉」（不影響背景 job 繼續跑）。
    _run({ ..._scopeBody(), prompt_versions: promptVersions });
  };

  // ── 單列操作（操作欄；與批量 selectedKeys 完全解耦，各自獨立路徑）──
  /** 進行中的單列 id 集合（控制該列按鈕 loading）。 */
  const rowBusy = ref<Set<string>>(new Set());
  const isRowBusy = (id: string) => rowBusy.value.has(id);
  const _setBusy = (id: string, busy: boolean) => {
    const s = new Set(rowBusy.value);
    if (busy) s.add(id);
    else s.delete(id);
    rowBusy.value = s;
  };

  /**
   * 單列（重）判：對該列跑初判歸因（複用 startPrejudge，item_ids 僅此列），等 SSE done 就地重載。
   * 走該列按鈕自己的 inline loading（isRowBusy）。與批量共用 logEntries/logStreaming/jobStatus/
   * progress（抽屜進度列與執行日誌即時反映；頁面頂部批量大進度條 gated by `running`，單列不設
   * running 故不會誤顯示）——共寫安全靠雙向互斥：批量進行中擋單列、任一單列進行中擋批量與其他
   * 單列（見本函式與 _run 開頭 guard）。
   * @param promptVersions 版本選擇功能：指定的 {rule_code: 版本號}（未指定沿用 active）。
   */
  const rejudgeRow = async (id: string, promptVersions?: Record<string, number>) => {
    if (rowBusy.value.size) {
      // 共用同一份進度/日誌狀態，同時只允許一個單列初判
      Message.warning('已有單列初判進行中，請稍後再試');
      return;
    }
    if (running.value) {
      // 批次初判進行中，避免與批次 job 並發對同一 finding 送出重複初判（重複花費 / 結果互相覆蓋）
      Message.warning('批次初判進行中，請稍後再試');
      return;
    }
    _setBusy(id, true);
    jobStatus.value = 'running';
    progress.value = { processed: 0, total: 1, totalTokens: 0, costUsd: 0 };
    try {
      const r = await startPrejudge({
        item_ids: [id],
        source: toValue(source),
        llm_config_id: llmConfigId.value || undefined,
        prompt_versions: promptVersions,
      });
      _openLog(r.job_id);
      await _poll(r.job_id);
      await reload();
      if (jobStatus.value === 'done') {
        Message.success(`已完成歸因（模型 ${r.model}）`);
      } else if (jobStatus.value === 'error') {
        Message.error('歸因任務失敗（詳見執行日誌）');
      } else {
        Message.warning('進度連線中斷：任務可能仍在背景執行，稍後重新整理列表確認結果');
      }
    } catch (e: any) {
      Message.error('歸因失敗：' + (e?.message || e));
    } finally {
      _setBusy(id, false);
      jobStatus.value = '';
      _closeLog();
    }
  };

  /** 重新初判本批失敗筆：收 failed_items 的 item_id 走既有 item_ids 顯式重新初判路徑（失敗筆未落快取，天然不吃 cache）。 */
  const retryFailed = () => {
    if (running.value) {
      Message.warning('批次初判進行中，請稍後再試');
      return;
    }
    const ids = failedItems.value.map((f) => f.item_id).filter(Boolean);
    if (!ids.length) {
      Message.info('沒有失敗筆可重新初判');
      return;
    }
    _run({ item_ids: ids, source: toValue(source) });
  };

  return {
    running,
    jobStatus,
    progress,
    progressPct,
    costText,
    logEntries,
    logStreaming,
    logError,
    failedItems,
    failedTruncated,
    retryFailed,
    confirmOpen,
    targetMode,
    targetStages,
    lowConfOnly,
    draftFilters,
    targetCount,
    hasJudgedStage,
    refreshTargetCount,
    openPrejudge,
    doRun,
    pauseJob,
    resumeJob,
    cancelJob,
    isRowBusy,
    rejudgeRow,
  };
}

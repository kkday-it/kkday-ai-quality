// 初判歸因批次任務：跑批 + SSE 進度 + 暫停/恢復/停止 + 目標選取彈窗 + 單列重判。
// 自 useAttributionList 下沉；依賴（來源 / 選中模型 / 垂直分類 / 勾選列 / 重載回呼）由呼叫端注入，
// 回傳之 ref 保留原 identity（呼叫端 spread 進 return，模板綁定不變）。
import {
  computed,
  reactive,
  ref,
  toValue,
  type ComputedRef,
  type MaybeRefOrGetter,
  type Ref,
} from 'vue';
import { Message } from '@arco-design/web-vue';
import judgment from '@config/ai_judge/judgment.json';
import {
  cancelPrejudge,
  pausePrejudge,
  prejudgeStreamUrl,
  previewPrejudgeCount,
  resumePrejudge,
  startPrejudge,
  type PrejudgeBody,
} from '@/api';
import { emptyFilters, filtersToParams, STAGE_LABELS } from '../constants';

/** 頁面列表篩選快照（開彈窗時自動帶入彈窗內目標篩選草稿的初值；鍵名對齊 getProblems 參數）。 */
export interface PrejudgeListFilters {
  /** 傾向（頁面傾向多選篩選；開彈窗時取第一項作為再判收斂傾向的預設值）。 */
  polarity?: string[];
  /** 信心分層（判決級，僅已判分支）。 */
  confidenceTier?: string;
  /** 歸因分類（判決級，僅已判分支；多選任意層級 code，子樹語義）。 */
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
  /** 判決完成後重載列表 + 未判數（就地看到結果）。 */
  reload: () => Promise<void>;
}

/**
 * 初判歸因批次任務控制。
 * @returns 進度狀態、目標選取、批次動作（跑/暫停/恢復/停止）、單列重判 loading + 觸發。
 */
export function usePrejudgeJob(deps: PrejudgeJobDeps) {
  const { source, llmConfigId, effVerticals, selectedKeys, listFilters, reload } = deps;

  const running = ref(false);
  /** 當前 job_id（供暫停/恢復/停止；無執行中為空）。 */
  const jobId = ref('');
  /** 當前 job 狀態（running/paused/cancelling/cancelled/done/error；由 SSE 權威更新，動作先樂觀設）。 */
  const jobStatus = ref('');
  const progress = ref({ processed: 0, total: 0, totalTokens: 0, costUsd: 0 });
  const progressPct = computed(() =>
    progress.value.total ? Math.round((progress.value.processed / progress.value.total) * 100) : 0,
  );
  /** token 花費顯示（金額 4 位小數，token 千分位）；批量判決過程同步更新。 */
  const costText = computed(() =>
    progress.value.totalTokens
      ? `${progress.value.totalTokens.toLocaleString()} tokens · ≈ $${progress.value.costUsd.toFixed(4)}`
      : '',
  );
  // 以 SSE 長連線接收批量判決進度（取代 setInterval 輪詢）；done/error 或連線中斷即 resolve。
  const _poll = (jid: string) =>
    new Promise<void>((resolve) => {
      const es = new EventSource(prejudgeStreamUrl(jid));
      const finish = () => {
        es.close();
        resolve();
      };
      es.onmessage = (ev) => {
        const st = JSON.parse(ev.data);
        jobStatus.value = st.status || jobStatus.value; // SSE 權威狀態（涵蓋 paused/cancelling）
        progress.value = {
          processed: st.processed || 0,
          total: st.total || progress.value.total,
          totalTokens: st.total_tokens || 0,
          costUsd: st.cost_usd || 0,
        };
        if (st.status === 'done' || st.status === 'error' || st.status === 'cancelled') finish();
      };
      es.onerror = finish;
    });
  const _run = async (body: PrejudgeBody) => {
    if (running.value) return;
    running.value = true;
    jobStatus.value = 'running';
    progress.value = { processed: 0, total: 0, totalTokens: 0, costUsd: 0 };
    try {
      const r = await startPrejudge({ ...body, llm_config_id: llmConfigId.value || undefined });
      jobId.value = r.job_id;
      progress.value = { processed: 0, total: r.total, totalTokens: 0, costUsd: 0 };
      if (!r.total) {
        Message.warning('沒有可分析的對象');
        return;
      }
      await _poll(r.job_id);
      if (jobStatus.value === 'cancelled') {
        Message.info(
          `已停止：已處理 ${progress.value.processed}/${progress.value.total} 筆（已判結果保留）`,
        );
      } else {
        Message.success(`初判歸因完成：${progress.value.processed} 筆（模型 ${r.model}）`);
      }
      await reload(); // 重載當前頁（保持頁碼，就地看到結果）
    } catch (e: any) {
      Message.error('初判歸因失敗：' + (e?.message || e));
    } finally {
      running.value = false;
      jobId.value = '';
      jobStatus.value = '';
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
  /** 停止當前 job（不再派新工，已在跑的收斂後轉 cancelled；已判已落庫）。 */
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
  const targetStages = ref<string[]>(['unjudged']); // 預設只收未判
  const lowConfOnly = ref(true); // true＝僅低信心(<auto_accept)；false＝全部信心
  const targetCount = ref(0); // 「將處理 N 筆」預覽
  // ── 彈窗內目標篩選草稿：開彈窗自動帶入頁面當前列表篩選，彈窗內可下拉重選，只影響本次判決目標 ──
  // 與列表同形狀（AttributionFilters）→ 彈窗 AttributionFilterBar 直接綁定（統一完整篩選欄）。
  // 表級（dateRange/oid/hasExternal）兩分支皆套；判決級（polarity/tier/taxonomy）只對已判分支送
  // （未判列無判決）。stage 由上方 checkbox 承擔 → 此欄不納入彈窗篩選。
  const draftFilters = reactive(emptyFilters());
  /** 是否含已判階段（非 unjudged）→ 顯示傾向/信心收斂條件。 */
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
            max_confidence: lowConfOnly.value ? judgment.confidence_tiers.auto_accept : undefined,
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
   *  無勾選＝全部資料且只判未判、再判收斂預設負向+僅低信心（安全預設，避免誤重判全庫）。 */
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
    // 傾向：帶入列表當前傾向；無選取的預設 scope（只判未判）用 negative 兜底——
    // 避免使用者臨時加勾已判階段卻沒選傾向時，把整庫已判全數重判。
    draftFilters.polarity = hasSel ? [] : lf.polarity?.length ? [...lf.polarity] : ['negative'];
    lowConfOnly.value = !hasSel;
    confirmOpen.value = true;
    void refreshTargetCount();
  };

  /** 二次確認後執行：範圍（全部/已選內）+ 階段 + 篩選草稿統一走 scope 目標選取（與預覽同 body）。 */
  const doRun = () => {
    confirmOpen.value = false;
    _run(_scopeBody());
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
   * 走該列按鈕 inline loading，不觸發頁頂大進度條（不設 running，_poll 僅更新無人顯示的 progress）。
   */
  const rejudgeRow = async (id: string) => {
    if (rowBusy.value.has(id)) return;
    if (running.value) {
      // 批次判決進行中，避免與批次 job 並發對同一 finding 送出重複判決（重複花費 / 結果互相覆蓋）
      Message.warning('批次判決進行中，請稍後再試');
      return;
    }
    _setBusy(id, true);
    try {
      const r = await startPrejudge({
        item_ids: [id],
        source: toValue(source),
        llm_config_id: llmConfigId.value || undefined,
      });
      await _poll(r.job_id);
      await reload();
      Message.success(`已完成歸因（模型 ${r.model}）`);
    } catch (e: any) {
      Message.error('歸因失敗：' + (e?.message || e));
    } finally {
      _setBusy(id, false);
    }
  };

  return {
    running,
    jobStatus,
    progress,
    progressPct,
    costText,
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

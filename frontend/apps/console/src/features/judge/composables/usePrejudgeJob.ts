// 初判歸因批次任務：跑批 + SSE 進度 + 暫停/恢復/停止 + 目標選取彈窗 + 單列重判。
// 自 useAttributionList 下沉；依賴（來源 / 選中模型 / 垂直分類 / 勾選列 / 重載回呼）由呼叫端注入，
// 回傳之 ref 保留原 identity（呼叫端 spread 進 return，模板綁定不變）。
import { computed, ref, toValue, type ComputedRef, type MaybeRefOrGetter, type Ref } from 'vue';
import { Message } from '@arco-design/web-vue';
import judgment from '@config/ai_judge/judgment.json';
import {
  cancelPrejudge,
  getProblems,
  pausePrejudge,
  prejudgeStreamUrl,
  resumePrejudge,
  startPrejudge,
} from '@/api';

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
  /** 判決完成後重載列表 + 未判數（就地看到結果）。 */
  reload: () => Promise<void>;
}

/**
 * 初判歸因批次任務控制。
 * @returns 進度狀態、目標選取、批次動作（跑/暫停/恢復/停止）、單列重判 loading + 觸發。
 */
export function usePrejudgeJob(deps: PrejudgeJobDeps) {
  const { source, llmConfigId, effVerticals, selectedKeys, reload } = deps;

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
  const _run = async (body: {
    item_ids?: string[];
    source?: string;
    scope?: string;
    product_verticals?: string[];
    stages?: string[];
    target_polarity?: string;
    max_confidence?: number;
  }) => {
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
        Message.info(`已停止：已處理 ${progress.value.processed}/${progress.value.total} 筆（已判結果保留）`);
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
  /** 目標模式：selected＝用勾選列；scope＝依判決階段選取。有勾選預設 selected，否則 scope。 */
  const targetMode = ref<'selected' | 'scope'>('scope');
  const targetStages = ref<string[]>(['unjudged']); // 預設只收未判
  const targetPolarity = ref('negative'); // 再判傾向收斂（勾已判階段時生效）
  const lowConfOnly = ref(true); // true＝僅低信心(<auto_accept)；false＝全部信心
  const targetCount = ref(0); // 「將處理 N 筆」預覽
  /** 是否含已判階段（非 unjudged）→ 顯示傾向/信心收斂條件。 */
  const hasJudgedStage = computed(() => targetStages.value.some((s) => s !== 'unjudged'));

  // 單調遞增請求序號：快速切換條件會併發多次 refresh，僅最後一次可寫入 targetCount（防慢回應覆蓋新值）。
  let countSeq = 0;
  /** 依目標模式/條件算「將處理 N 筆」預覽（scope 模式逐階段查 getProblems total 加總；信心收斂無法由列表 API 精算，屬近似）。 */
  const refreshTargetCount = async () => {
    if (targetMode.value === 'selected') {
      targetCount.value = selectedKeys.value.length;
      return;
    }
    const seq = ++countSeq;
    let total = 0;
    for (const st of targetStages.value) {
      const r = await getProblems({
        source: toValue(source),
        productVerticals: effVerticals.value,
        limit: 1,
        ...(st === 'unjudged'
          ? { judged: false }
          : { stage: [st], ...(targetPolarity.value ? { polarity: targetPolarity.value } : {}) }),
      });
      total += r.total || 0;
    }
    if (seq === countSeq) targetCount.value = total; // 過期回應（已有更新的 refresh）丟棄，不覆蓋
  };

  /** 開初判歸因彈窗：有勾選預設 selected 模式，否則 scope 模式。 */
  const openPrejudge = () => {
    targetMode.value = selectedKeys.value.length ? 'selected' : 'scope';
    confirmOpen.value = true;
    void refreshTargetCount();
  };

  /** 二次確認後執行：selected→item_ids（帶 source，後端據此選對專表）；scope→stage 驅動目標選取。 */
  const doRun = () => {
    confirmOpen.value = false;
    if (targetMode.value === 'selected') {
      _run({ item_ids: selectedKeys.value, source: toValue(source) });
      return;
    }
    _run({
      source: toValue(source),
      scope: 'all',
      product_verticals: effVerticals.value,
      stages: targetStages.value,
      ...(hasJudgedStage.value
        ? {
            target_polarity: targetPolarity.value || undefined,
            max_confidence: lowConfOnly.value ? judgment.confidence_tiers.auto_accept : undefined,
          }
        : {}),
    });
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
    targetPolarity,
    lowConfOnly,
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

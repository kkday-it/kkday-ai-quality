// 歸因列表資料與互動邏輯（分頁 / 篩選 / 選取 / 初判歸因批次 / CSV 導出）——由 AttributionList.vue 下沉，
// 使頁面薄化為模板+綁定；來源切換時整組篩選按新 schema 清空殘留值。
import { computed, ref, toValue, watch, type MaybeRefOrGetter } from 'vue';
import judgment from '@config/ai_judge/judgment.json';
import {
  cancelPrejudge,
  startProblemsExport,
  pausePrejudge,
  patchStatus,
  prejudgeStreamUrl,
  getL1Domains,
  getProblems,
  resumePrejudge,
  startPrejudge,
  type L1DomainOpt,
} from '@/api';
import { Message } from '@arco-design/web-vue';
import { useVerticalFilterStore } from '@/stores';
import { schemaFor, type Attribution, type ProblemRow } from '../constants';
import { exportName } from '../utils';
import { useExportJob } from './useExportJob';
import { useLlmConfigs } from './useLlmConfigs';

/**
 * 歸因列表的資料載入、篩選、選取與初判歸因批次邏輯。
 *
 * @param source 目前選定來源（可為 ref / getter / 純值；切換時自動清空頁碼/選取/殘留篩選並重載）。
 * @returns 分頁資料、篩選狀態、選取操作、初判歸因批次控制、CSV 導出。
 */
export function useAttributionList(source: MaybeRefOrGetter<string>) {
  const schema = computed(() => schemaFor(toValue(source)));

  // ── 全局商品垂直分類篩選（兩層 SSOT）：工具列複選＝實際篩選；可選分類＝規則配置頁設定的選項池──
  const verticalFilter = useVerticalFilterStore();
  /** 工具列可選分類＝規則配置頁設定的選項池（總 list）。 */
  const verticalOptions = computed(() => verticalFilter.toolbarOptions);
  /** 工具列篩選選中（複選；預設全選選項池，剩 1 不可移除，由 store.setFilter 守衛）。 */
  const verticalGroups = computed(() => verticalFilter.filter);
  /** 生效的垂直分類（送查詢用）：全 pool/未選＝不篩選（回 undefined），子集才送分組名。 */
  const effVerticals = computed<string[] | undefined>(() =>
    verticalFilter.activeGroups.length ? [...verticalFilter.activeGroups] : undefined,
  );
  /** 複選變更：剩 1 不可移除（清空由 store.setFilter 忽略）；寫回全局 store（列表/縱覽/scope 同步）。 */
  const onVerticalChange = (v: unknown) => {
    verticalFilter.setFilter(Array.isArray(v) ? (v as string[]) : []);
  };

  // ── 篩選狀態（各來源 schema 決定哪些生效；切來源時一併清空）──
  const polarityFilter = ref('');
  const scoreFilter = ref<number[]>([]); // 星等多選（1-5；僅有 score_col 的來源生效）
  const stageFilter = ref<string[]>([]); // 判決階段多選（STAGE_LABELS 五值）
  const tierFilter = ref(''); // 信心分層單選（TIER_LABELS）
  const l1Filter = ref(''); // L1 歸因域單選（l1_domain_code）
  const l1Options = ref<L1DomainOpt[]>([]); // L1 域下拉選項（該來源已判資料 distinct）
  /** 載入 L1 域選項（來源切換 / 初始）；失敗回空不阻斷列表。 */
  const loadL1Options = async () => {
    try {
      l1Options.value = await getL1Domains(toValue(source));
    } catch {
      l1Options.value = [];
    }
  };
  const dateRange = ref<string[]>([]);
  const prodOidFilter = ref('');
  const orderOidFilter = ref('');
  /** 排序狀態（'欄位:方向'，欄位∈occurred_at/score/go_date/confidence）；預設評論時間新到舊。 */
  const sortValue = ref('occurred_at:desc');
  /** 生效的 polarity 篩選（送後端；空＝不篩）。「僅看問題」已移除，傾向下拉直接涵蓋負向。 */
  const effPolarity = computed(() => polarityFilter.value || undefined);

  // ── LLM 模型（已保存配置）──下沉 useLlmConfigs（載入/選中）；同源「設定 › LLM 模型連線」。
  const { llmConfigId, llmConfigs, loadConfigs } = useLlmConfigs();

  // ── 伺服器端分頁 ──
  const rows = ref<ProblemRow[]>([]);
  const total = ref(0);
  const unjudged = ref(0);
  const page = ref(1);
  const pageSize = ref(20);
  const loading = ref(true);
  const error = ref('');

  /**
   * 列表 / 分頁選取共用的篩選+排序查詢參數（不含 limit/offset）。
   * 抽為單一來源避免 loadPage / selectPages 兩處各寫一份、加新篩選時漏改而 drift。
   */
  const filterQuery = () => {
    const [sortBy, sortDir] = sortValue.value.split(':');
    return {
      source: toValue(source),
      polarity: effPolarity.value,
      scores: scoreFilter.value.length ? scoreFilter.value : undefined,
      stage: stageFilter.value.length ? stageFilter.value : undefined,
      confidenceTier: tierFilter.value || undefined,
      l1Domain: l1Filter.value || undefined,
      productVerticals: effVerticals.value,
      dateFrom: dateRange.value?.[0] || undefined,
      dateTo: dateRange.value?.[1] || undefined,
      prodOid: prodOidFilter.value.trim() || undefined,
      orderOid: orderOidFilter.value.trim() || undefined,
      sortBy: sortBy || undefined,
      sortDir: (sortDir as 'asc' | 'desc') || 'desc',
    };
  };

  const loadPage = async () => {
    loading.value = true;
    error.value = '';
    try {
      const r = await getProblems({
        ...filterQuery(),
        limit: pageSize.value,
        offset: (page.value - 1) * pageSize.value,
      });
      rows.value = r.rows || [];
      total.value = r.total || 0;
      // 一列一 review（多歸因收進 row.attributions 陣列）；rowKey 綁 _group（source_id）
    } catch (e: any) {
      error.value = '載入失敗：' + (e?.message || e);
    } finally {
      loading.value = false;
    }
  };
  /** Arco 表頭點擊排序變更 → 映射後端 sort_by/sort_dir；清除排序（direction 空）回預設評論時間新→舊。 */
  const onSortChange = (dataIndex: string, direction: string) => {
    sortValue.value = direction
      ? `${dataIndex}:${direction === 'ascend' ? 'asc' : 'desc'}`
      : 'occurred_at:desc';
    onFilterChange();
  };

  /** 生效篩選項數（供工具列「已套用 N 項」提示；不含排序）。 */
  const activeFilterCount = computed(
    () =>
      (polarityFilter.value ? 1 : 0) +
      (scoreFilter.value.length ? 1 : 0) +
      (stageFilter.value.length ? 1 : 0) +
      (tierFilter.value ? 1 : 0) +
      (l1Filter.value ? 1 : 0) +
      (dateRange.value?.length ? 1 : 0) +
      (prodOidFilter.value.trim() ? 1 : 0) +
      (orderOidFilter.value.trim() ? 1 : 0),
  );

  /** 重置所有篩選 + 排序（回預設）並重載第 1 頁。 */
  const resetFilters = () => {
    polarityFilter.value = '';
    scoreFilter.value = [];
    stageFilter.value = [];
    tierFilter.value = '';
    l1Filter.value = '';
    dateRange.value = [];
    prodOidFilter.value = '';
    orderOidFilter.value = '';
    sortValue.value = 'occurred_at:desc';
    onFilterChange();
  };
  /** 取未判筆數（全部未判按鈕顯示）。 */
  const loadUnjudged = async () => {
    try {
      const r = await getProblems({
        source: toValue(source),
        judged: false,
        productVerticals: effVerticals.value,
        limit: 1,
      });
      unjudged.value = r.total || 0;
    } catch {
      unjudged.value = 0;
    }
  };
  /** 篩選變動：回第 1 頁重載。 */
  const onFilterChange = () => {
    page.value = 1;
    selectedKeys.value = [];
    loadPage();
    loadUnjudged();
  };

  // 切換來源：整組篩選按新 schema 清空殘留值（避免舊來源篩選值誤帶入新來源查詢）
  watch(
    () => toValue(source),
    () => {
      const filterTypes = new Set(schema.value.filters.map((f) => f.type));
      if (!filterTypes.has('polarity')) polarityFilter.value = '';
      if (!filterTypes.has('score')) scoreFilter.value = [];
      if (!filterTypes.has('stage')) stageFilter.value = [];
      if (!filterTypes.has('tier')) tierFilter.value = '';
      if (!filterTypes.has('l1Domain')) l1Filter.value = '';
      if (!filterTypes.has('dateRange')) dateRange.value = [];
      // prod_oid / order_oid / 排序為通用能力（非 schema-gated），切來源一律歸零避免誤帶
      prodOidFilter.value = '';
      orderOidFilter.value = '';
      sortValue.value = 'occurred_at:desc';
      loadL1Options(); // L1 選項隨來源重載
      onFilterChange();
    },
  );

  // 全局垂直分類（選中）變更 → 列表 + 未判 count 即時重載（縱覽頁另有自己的 watch）。
  watch(
    () => verticalFilter.filter,
    () => onFilterChange(),
    { deep: true },
  );

  const init = () => {
    loadConfigs();
    verticalFilter.loadOptions();
    loadL1Options();
    loadPage();
    loadUnjudged();
  };

  // ── 選取（跨頁累積；rowKey=source_id → selectedKeys 即勾選 key，doRun/export/selectPages 直接用）──
  const selectedKeys = ref<string[]>([]); // source_id（該來源特徵 id）
  const runCount = computed(() => selectedKeys.value.length); // 已選 review 數
  const clearSelection = () => (selectedKeys.value = []);
  /** 表格 selectedRowKeys＝業務 selectedKeys（rowKey=source_id，一列一 review，無需映射）。 */
  const selectedRowKeys = selectedKeys;
  /** 表格勾選變更（rowKey=source_id）：合併保留非本頁既有選取（跨頁）。 */
  const onSelectionChange = (keys: (string | number)[]) => {
    const pageGroups = new Set(rows.value.map((r) => String(r._group)));
    selectedKeys.value = [
      ...selectedKeys.value.filter((id) => !pageGroups.has(id)), // 保留非本頁選取
      ...keys.map((k) => String(k)), // 本頁已勾（key 即 source_id）
    ];
  };
  const pageSpec = ref('');
  /** 分頁選取（1,2,3,5 / 1~200）：依後端分頁抓對應頁的 item_id 加入選取。 */
  const selectPages = async () => {
    const spec = pageSpec.value.trim();
    if (!spec) return;
    const pages = new Set<number>();
    for (const part of spec.split(/[,，]/)) {
      const seg = part.trim();
      if (!seg) continue;
      const m = seg.split(/[~\-～]/);
      if (m.length === 2 && +m[0] && +m[1]) {
        for (let p = Math.min(+m[0], +m[1]); p <= Math.max(+m[0], +m[1]); p++) pages.add(p);
      } else if (+seg) {
        pages.add(+seg);
      }
    }
    if (!pages.size) return;
    const lo = Math.min(...pages);
    const hi = Math.max(...pages);
    const ps = pageSize.value;
    try {
      const r = await getProblems({
        ...filterQuery(),
        limit: (hi - lo + 1) * ps,
        offset: (lo - 1) * ps,
      });
      const ids: string[] = [];
      (r.rows || []).forEach((row: ProblemRow, idx: number) => {
        const gp = lo + Math.floor(idx / ps); // 該列的全域分頁號
        if (pages.has(gp)) ids.push(String(row._group)); // 特徵 id（source_id）
      });
      selectedKeys.value = Array.from(new Set([...selectedKeys.value, ...ids]));
      Message.success(`已選取 ${ids.length} 列（分頁 ${spec}）`);
    } catch (e: any) {
      Message.error('分頁選取失敗：' + (e?.message || e));
    }
  };

  // ── 初判歸因 ──
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
  const _poll = (jobId: string) =>
    new Promise<void>((resolve) => {
      const es = new EventSource(prejudgeStreamUrl(jobId));
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
      await loadPage(); // 重載當前頁（保持頁碼，就地看到結果）
      await loadUnjudged();
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

  /** 依目標模式/條件算「將處理 N 筆」預覽（scope 模式逐階段查 getProblems total 加總；信心收斂無法由列表 API 精算，屬近似）。 */
  const refreshTargetCount = async () => {
    if (targetMode.value === 'selected') {
      targetCount.value = selectedKeys.value.length;
      return;
    }
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
    targetCount.value = total;
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

  // ── 導出（背景 job + SSE 實時進度 + 可停止；有勾選→只導已選 review，否則導符合目前篩選全部）──
  const exportJob = useExportJob();
  /** 啟動導出：交由 useExportJob 管進度/下載/停止；1:N 每條歸因一列的美化 xlsx。 */
  const exportCsv = () =>
    exportJob.run(
      () =>
        startProblemsExport({
          source: toValue(source),
          polarity: effPolarity.value,
          scores: scoreFilter.value.length ? scoreFilter.value : undefined,
          product_verticals: effVerticals.value,
          date_from: dateRange.value?.[0] || undefined,
          date_to: dateRange.value?.[1] || undefined,
          item_ids: selectedKeys.value.length ? selectedKeys.value : undefined,
        }),
      exportName('歸因列表', 'xlsx'),
    );

  // ── 單列操作（操作欄；與批量 selectedKeys 完全解耦，各自獨立路徑）──
  /** 進行中的單列 id 集合（歸因/覆核共用，控制該列按鈕 loading）。 */
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
    _setBusy(id, true);
    try {
      const r = await startPrejudge({
        item_ids: [id],
        source: toValue(source),
        llm_config_id: llmConfigId.value || undefined,
      });
      await _poll(r.job_id);
      await loadPage();
      await loadUnjudged();
      Message.success('已完成歸因');
    } catch (e: any) {
      Message.error('歸因失敗：' + (e?.message || e));
    } finally {
      _setBusy(id, false);
    }
  };

  /**
   * 單條歸因覆核：只改該 finding 的 status（per-attribution；每條歸因分開操作）。
   * optimistic 即時回寫（PATCH 秒級，仿 FindingCard 無 loading）；只改人工 status 軸、不動 AI stage。
   */
  const reviewFinding = async (attr: Attribution, status: string) => {
    if (!attr.finding_id) return;
    try {
      await patchStatus(attr.finding_id, status);
      attr.status = status; // optimistic：覆核徽章即時反映
      Message.success(status === 'confirmed' ? '已確認' : '已忽略');
    } catch (e: any) {
      Message.error('覆核失敗：' + (e?.message || e));
    }
  };

  return {
    schema,
    // 篩選
    polarityFilter,
    scoreFilter,
    stageFilter,
    tierFilter,
    l1Filter,
    l1Options,
    dateRange,
    prodOidFilter,
    orderOidFilter,
    verticalOptions,
    verticalGroups,
    onVerticalChange,
    onSortChange,
    onFilterChange,
    activeFilterCount,
    resetFilters,
    // 模型
    llmConfigId,
    llmConfigs,
    // 分頁資料
    rows,
    total,
    unjudged,
    page,
    pageSize,
    loading,
    error,
    loadPage,
    // 選取
    selectedKeys,
    selectedRowKeys,
    onSelectionChange,
    runCount,
    clearSelection,
    pageSpec,
    selectPages,
    // 初判歸因
    running,
    jobStatus,
    progress,
    progressPct,
    costText,
    confirmOpen,
    openPrejudge,
    targetMode,
    targetStages,
    targetPolarity,
    lowConfOnly,
    targetCount,
    hasJudgedStage,
    refreshTargetCount,
    doRun,
    pauseJob,
    resumeJob,
    cancelJob,
    // 單列操作（操作欄）
    isRowBusy,
    rejudgeRow,
    reviewFinding,
    // 導出（背景 job + 實時進度 + 停止）
    exportCsv,
    exporting: exportJob.exporting,
    exportStatus: exportJob.status,
    exportProgress: exportJob.progress,
    exportPct: exportJob.pct,
    cancelExport: exportJob.cancel,
    // 初始化（onMounted 呼叫）
    init,
  };
}

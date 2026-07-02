// 歸因列表資料與互動邏輯（分頁 / 篩選 / 選取 / 初判歸因批次 / CSV 導出）——由 AttributionList.vue 下沉，
// 使頁面薄化為模板+綁定；來源切換時整組篩選按新 schema 清空殘留值。
import { computed, ref, toValue, watch, type MaybeRefOrGetter } from 'vue';
import {
  exportProblems,
  getProductVerticalResolved,
  prejudgeStreamUrl,
  getProblems,
  getSettings,
  startPrejudge,
} from '@/api';
import { Message } from '@arco-design/web-vue';
import { schemaFor, type ProblemRow } from '../constants';

/** LLM 模型配置選項（同「設定 › LLM 模型連線」）。 */
interface LlmConfigOpt {
  id: string;
  provider: string;
  model: string;
  reasoning_effort: string;
}

/**
 * 正規化時間字串顯示：去小數秒/去 T·Z；dateOnly 或時間為 00:00:00 時只留日期。
 * 與後端 db.fmt_datetime 語義一致（評論時間含時分秒、出發日只到日）。
 * @param value 原始時間字串（可能為 null/undefined）
 * @param dateOnly 是否強制只顯示日期
 * @returns 正規化後字串（無值回傳空字串）
 */
export const fmtDt = (value: unknown, dateOnly = false): string => {
  if (value === null || value === undefined || value === '') return '';
  let s = String(value).trim().replace('T', ' ');
  if (s.endsWith('Z')) s = s.slice(0, -1).trim();
  s = s.replace(/\.\d+/, ''); // 去小數秒
  if (dateOnly || s.endsWith(' 00:00:00')) return s.split(' ')[0];
  return s;
};

/**
 * 歸因列表的資料載入、篩選、選取與初判歸因批次邏輯。
 *
 * @param source 目前選定來源（可為 ref / getter / 純值；切換時自動清空頁碼/選取/殘留篩選並重載）。
 * @returns 分頁資料、篩選狀態、選取操作、初判歸因批次控制、CSV 導出。
 */
export function useAttributionList(source: MaybeRefOrGetter<string>) {
  const schema = computed(() => schemaFor(toValue(source)));

  // ── 篩選狀態（各來源 schema 決定哪些生效；切來源時一併清空）──
  const polarityFilter = ref('');
  const onlyProblem = ref(false);
  const scoreFilter = ref<number[]>([]);
  const productVerticalFilter = ref<string[]>([]);
  const dateRange = ref<string[]>([]);
  const prodOidFilter = ref('');
  const orderOidFilter = ref('');
  /** 排序狀態（'欄位:方向'，欄位∈occurred_at/score/go_date/confidence）；預設評論時間新到舊。 */
  const sortValue = ref('occurred_at:desc');
  /** 生效的 polarity 篩選（送後端）。 */
  const effPolarity = computed(() =>
    onlyProblem.value ? 'negative' : polarityFilter.value || undefined,
  );

  // ── 商品垂直分類選項（來自 config，動態解析）──
  const productVerticalOpts = ref<string[]>([]);
  const loadProductVerticals = async () => {
    try {
      const r = await getProductVerticalResolved();
      productVerticalOpts.value = Object.keys(r.groups || {});
    } catch {
      productVerticalOpts.value = [];
    }
  };

  // ── LLM 模型（已保存配置）──
  const llmConfigId = ref('');
  // 與「設定 › LLM 模型連線」同源（backend settings.llm_configs）；顯示名共用 composeLlmLabel 確保同步
  const llmConfigs = ref<LlmConfigOpt[]>([]);
  const loadConfigs = async () => {
    try {
      const s = await getSettings();
      llmConfigs.value = (s.llm_configs || []).map((c: any) => ({
        id: c.id,
        provider: c.provider || '',
        model: c.model || '',
        reasoning_effort: c.reasoning_effort || 'default',
      }));
      llmConfigId.value = s.active_llm_config_id || llmConfigs.value[0]?.id || '';
    } catch {
      llmConfigs.value = [];
    }
  };

  // ── 伺服器端分頁 ──
  const rows = ref<ProblemRow[]>([]);
  const total = ref(0);
  const unjudged = ref(0);
  const page = ref(1);
  const pageSize = ref(20);
  const loading = ref(true);
  const error = ref('');
  /** 展開列 key（受控）；每次載入預設全展開，「一鍵收合」清空。 */
  const expandedKeys = ref<string[]>([]);

  const loadPage = async () => {
    loading.value = true;
    error.value = '';
    const [sortBy, sortDir] = sortValue.value.split(':');
    try {
      const r = await getProblems({
        source: toValue(source),
        polarity: effPolarity.value,
        scores: scoreFilter.value.length ? scoreFilter.value : undefined,
        productVerticals: productVerticalFilter.value.length ? productVerticalFilter.value : undefined,
        dateFrom: dateRange.value?.[0] || undefined,
        dateTo: dateRange.value?.[1] || undefined,
        prodOid: prodOidFilter.value.trim() || undefined,
        orderOid: orderOidFilter.value.trim() || undefined,
        sortBy: sortBy || undefined,
        sortDir: (sortDir as 'asc' | 'desc') || 'desc',
        limit: pageSize.value,
        offset: (page.value - 1) * pageSize.value,
      });
      rows.value = r.rows || [];
      total.value = r.total || 0;
      expandedKeys.value = rows.value.map((x) => x.item_id); // 載入後預設全展開
    } catch (e: any) {
      error.value = '載入失敗：' + (e?.message || e);
    } finally {
      loading.value = false;
    }
  };
  /** 一鍵收合 / 展開全部：依當前是否已全展開切換。 */
  const allExpanded = computed(() => rows.value.length > 0 && expandedKeys.value.length >= rows.value.length);
  const toggleExpandAll = () => {
    expandedKeys.value = allExpanded.value ? [] : rows.value.map((x) => x.item_id);
  };
  /** Arco 表頭點擊排序變更 → 映射後端 sort_by/sort_dir；清除排序（direction 空）回預設評論時間新→舊。 */
  const onSortChange = (dataIndex: string, direction: string) => {
    sortValue.value = direction
      ? `${dataIndex}:${direction === 'ascend' ? 'asc' : 'desc'}`
      : 'occurred_at:desc';
    onFilterChange();
  };

  /** 重置所有篩選 + 排序（回預設）並重載第 1 頁。 */
  const resetFilters = () => {
    polarityFilter.value = '';
    onlyProblem.value = false;
    scoreFilter.value = [];
    productVerticalFilter.value = [];
    dateRange.value = [];
    prodOidFilter.value = '';
    orderOidFilter.value = '';
    sortValue.value = 'occurred_at:desc';
    onFilterChange();
  };
  /** 取未判筆數（全部未判按鈕顯示）。 */
  const loadUnjudged = async () => {
    try {
      const r = await getProblems({ source: toValue(source), judged: false, limit: 1 });
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
      if (!filterTypes.has('polarity')) {
        polarityFilter.value = '';
        onlyProblem.value = false;
      }
      if (!filterTypes.has('score')) scoreFilter.value = [];
      if (!filterTypes.has('productVertical')) productVerticalFilter.value = [];
      if (!filterTypes.has('dateRange')) dateRange.value = [];
      // prod_oid / order_oid / 排序為通用能力（非 schema-gated），切來源一律歸零避免誤帶
      prodOidFilter.value = '';
      orderOidFilter.value = '';
      sortValue.value = 'occurred_at:desc';
      onFilterChange();
    },
  );

  const init = () => {
    loadConfigs();
    loadProductVerticals();
    loadPage();
    loadUnjudged();
  };

  // ── 選取（跨頁累積；row-key=item_id）──
  const selectedKeys = ref<string[]>([]);
  const runCount = computed(() => selectedKeys.value.length);
  const clearSelection = () => (selectedKeys.value = []);
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
      const [sortBy, sortDir] = sortValue.value.split(':');
      const r = await getProblems({
        source: toValue(source),
        polarity: effPolarity.value,
        scores: scoreFilter.value.length ? scoreFilter.value : undefined,
        productVerticals: productVerticalFilter.value.length ? productVerticalFilter.value : undefined,
        dateFrom: dateRange.value?.[0] || undefined,
        dateTo: dateRange.value?.[1] || undefined,
        prodOid: prodOidFilter.value.trim() || undefined,
        orderOid: orderOidFilter.value.trim() || undefined,
        sortBy: sortBy || undefined,
        sortDir: (sortDir as 'asc' | 'desc') || 'desc',
        limit: (hi - lo + 1) * ps,
        offset: (lo - 1) * ps,
      });
      const ids: string[] = [];
      (r.rows || []).forEach((row: ProblemRow, idx: number) => {
        const gp = lo + Math.floor(idx / ps); // 該列的全域分頁號
        if (pages.has(gp)) ids.push(row.item_id);
      });
      selectedKeys.value = Array.from(new Set([...selectedKeys.value, ...ids]));
      Message.success(`已選取 ${ids.length} 列（分頁 ${spec}）`);
    } catch (e: any) {
      Message.error('分頁選取失敗：' + (e?.message || e));
    }
  };

  // ── 初判歸因 ──
  const running = ref(false);
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
        progress.value = {
          processed: st.processed || 0,
          total: st.total || progress.value.total,
          totalTokens: st.total_tokens || 0,
          costUsd: st.cost_usd || 0,
        };
        if (st.status === 'done' || st.status === 'error') finish();
      };
      es.onerror = finish;
    });
  const _run = async (body: { item_ids?: string[]; source?: string; scope?: string }) => {
    if (running.value) return;
    running.value = true;
    progress.value = { processed: 0, total: 0, totalTokens: 0, costUsd: 0 };
    try {
      const r = await startPrejudge({ ...body, llm_config_id: llmConfigId.value || undefined });
      progress.value = { processed: 0, total: r.total, totalTokens: 0, costUsd: 0 };
      if (!r.total) {
        Message.warning('沒有可分析的對象');
        return;
      }
      await _poll(r.job_id);
      Message.success(`初判歸因完成：${progress.value.processed} 筆（模型 ${r.model}）`);
      await loadPage(); // 重載當前頁（保持頁碼，就地看到結果）
      await loadUnjudged();
    } catch (e: any) {
      Message.error('初判歸因失敗：' + (e?.message || e));
    } finally {
      running.value = false;
    }
  };
  // 二次確認彈窗：進行初判/全部未判皆先開彈窗，於其中選 model 配置再確認執行
  const confirmOpen = ref(false);
  const pendingScope = ref<'selected' | 'all'>('selected');
  const runSelected = () => {
    if (!selectedKeys.value.length) {
      Message.warning('請先勾選/分頁選取要分析的列');
      return;
    }
    pendingScope.value = 'selected';
    confirmOpen.value = true;
  };
  const runAll = () => {
    pendingScope.value = 'all';
    confirmOpen.value = true;
  };
  /** 二次確認後執行：依 pendingScope 決定範圍，用彈窗內選定的 llmConfigId。 */
  const doRun = () => {
    confirmOpen.value = false;
    if (pendingScope.value === 'selected') _run({ item_ids: selectedKeys.value });
    else _run({ source: toValue(source), scope: 'all' });
  };

  /** 導出 CSV（POST 全量；有勾選→只導已選，否則導符合目前篩選全部）→ blob 下載。 */
  const exportCsv = async () => {
    try {
      const blob = await exportProblems({
        source: toValue(source),
        polarity: effPolarity.value,
        scores: scoreFilter.value.length ? scoreFilter.value : undefined,
        product_verticals: productVerticalFilter.value.length ? productVerticalFilter.value : undefined,
        date_from: dateRange.value?.[0] || undefined,
        date_to: dateRange.value?.[1] || undefined,
        item_ids: selectedKeys.value.length ? selectedKeys.value : undefined,
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `歸因列表_${toValue(source)}_${selectedKeys.value.length || total.value}列.csv`;
      a.click();
      URL.revokeObjectURL(url);
      Message.success('已導出 CSV');
    } catch (e: any) {
      Message.error('導出失敗：' + (e?.message || e));
    }
  };

  return {
    schema,
    // 篩選
    polarityFilter,
    onlyProblem,
    scoreFilter,
    productVerticalFilter,
    productVerticalOpts,
    dateRange,
    prodOidFilter,
    orderOidFilter,
    onSortChange,
    onFilterChange,
    resetFilters,
    expandedKeys,
    allExpanded,
    toggleExpandAll,
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
    runCount,
    clearSelection,
    pageSpec,
    selectPages,
    // 初判歸因
    running,
    progress,
    progressPct,
    costText,
    confirmOpen,
    pendingScope,
    runSelected,
    runAll,
    doRun,
    // 導出
    exportCsv,
    // 初始化（onMounted 呼叫）
    init,
  };
}

// 歸因列表資料與互動邏輯（分頁 / 篩選 / 選取 / 初判歸因批次 / CSV 導出）——由 AttributionList.vue 下沉，
// 使頁面薄化為模板+綁定；來源切換時整組篩選按新 schema 清空殘留值。
import { computed, ref, toValue, watch, type MaybeRefOrGetter } from 'vue';
import {
  startProblemsExport,
  patchStatus,
  getL1Domains,
  getProblems,
  type L1DomainOpt,
} from '@/api';
import { Message } from '@arco-design/web-vue';
import { useVerticalFilterStore } from '@/stores';
import { schemaFor, type Attribution, type ProblemRow } from '../constants';
import { exportName } from '../utils';
import { useAttributionSelection } from './useAttributionSelection';
import { useExportJob } from './useExportJob';
import { useLlmConfigs } from './useLlmConfigs';
import { usePrejudgeJob } from './usePrejudgeJob';

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
  const recOidFilter = ref('');
  const prodOidFilter = ref('');
  const orderOidFilter = ref('');
  /** 排序狀態（'欄位:方向'，欄位∈occurred_at/score/go_date/confidence）；預設評論時間新到舊。 */
  const sortValue = ref('occurred_at:desc');
  /** 生效的 polarity 篩選（送後端；空＝不篩）。「僅看問題」已移除，傾向下拉直接涵蓋負向。 */
  const effPolarity = computed(() => polarityFilter.value || undefined);

  // ── LLM 模型（已保存配置）──下沉 useLlmConfigs（載入/選中/全域切換）；同源「設定 › LLM 模型連線」。
  const { llmConfigId, llmConfigs, activeLlmId, loadConfigs, setActiveLlm } = useLlmConfigs();

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
      recOid: recOidFilter.value.trim() || undefined,
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
      (recOidFilter.value.trim() ? 1 : 0) +
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
    recOidFilter.value = '';
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
      // rec_oid / prod_oid / order_oid / 排序為通用能力（非 schema-gated），切來源一律歸零避免誤帶
      recOidFilter.value = '';
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

  // ── 選取（跨頁累積；下沉 useAttributionSelection，注入 rows/pageSize/filterQuery；selectedKeys
  //    解構出來供 onFilterChange 清空 / exportCsv / usePrejudgeJob 直接用，維持原變數名不改呼叫端）──
  const selection = useAttributionSelection({ rows, pageSize, filterQuery });
  const { selectedKeys } = selection;

  // ── 初判歸因批次 + 單列重判（下沉 usePrejudgeJob；注入依賴，回傳 ref 保留 identity 不改綁定）──
  const job = usePrejudgeJob({
    source,
    llmConfigId,
    effVerticals,
    selectedKeys,
    reload: async () => {
      await loadPage();
      await loadUnjudged();
    },
  });

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

  // ── 單列覆核（操作欄；與批量 selectedKeys 解耦；單列重判已下沉 usePrejudgeJob.rejudgeRow）──
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
    recOidFilter,
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
    activeLlmId,
    setActiveLlm,
    // 分頁資料
    rows,
    total,
    unjudged,
    page,
    pageSize,
    loading,
    error,
    loadPage,
    // 選取（useAttributionSelection：selectedKeys/selectedRowKeys/onSelectionChange/runCount/clearSelection/pageSpec/selectPages）
    ...selection,
    // 初判歸因批次 + 單列重判（usePrejudgeJob：running/進度/目標/pause/resume/cancel/isRowBusy/rejudgeRow）
    ...job,
    // 單列覆核
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

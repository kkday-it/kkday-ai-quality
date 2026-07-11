// 歸因列表資料與互動邏輯（分頁 / 篩選 / 選取 / 初判歸因批次 / CSV 導出）——由 AttributionList.vue 下沉，
// 使頁面薄化為模板+綁定；來源切換時整組篩選按新 schema 清空殘留值。
import { computed, reactive, ref, toValue, watch, type MaybeRefOrGetter } from 'vue';
import {
  startProblemsExport,
  patchStatus,
  batchPatchStatus,
  getProblems,
  getTaxonomyCascade,
  type CascadeNode,
} from '@/api';
import { Message } from '@arco-design/web-vue';
import { useVerticalFilterStore } from '@/stores';
import {
  cloneFilters,
  countActiveFilters,
  emptyFilters,
  filtersToParams,
  schemaFor,
  type Attribution,
  type ProblemRow,
} from '../constants';
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

  // ── 篩選狀態（單一 reactive 物件＝SSOT；工具列/導出/初判共用 AttributionFilterBar 綁定此形狀）──
  // 各來源 schema 決定哪些欄位生效；切來源時一併清空殘留值（見下方 watch）。
  const filters = reactive(emptyFilters());
  const cascadeOptions = ref<CascadeNode[]>([]); // 歸因分類級聯選項（全局 L1→L3 樹，載一次共用）
  /** 載入歸因分類級聯樹（初始一次；全局分類與來源無關）；失敗回空不阻斷列表。 */
  const loadCascadeOptions = async () => {
    if (cascadeOptions.value.length) return;
    try {
      cascadeOptions.value = await getTaxonomyCascade();
    } catch {
      cascadeOptions.value = [];
    }
  };
  /** 排序狀態（'欄位:方向'，欄位∈occurred_at/score/go_date/confidence）；預設評論時間新到舊。 */
  const sortValue = ref('occurred_at:desc');

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
      ...filtersToParams(filters),
      productVerticals: effVerticals.value,
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
  const activeFilterCount = computed(() => countActiveFilters(filters));

  /** 重置所有篩選 + 排序（回預設）並重載第 1 頁。 */
  const resetFilters = () => {
    Object.assign(filters, emptyFilters());
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
      // schema-gated 欄位：新來源不支援者清空（type 名對齊 source-schema 的 filter type）
      if (!filterTypes.has('polarity')) filters.polarity = [];
      if (!filterTypes.has('stage')) filters.stage = [];
      if (!filterTypes.has('tier')) filters.tier = '';
      if (!filterTypes.has('status')) filters.status = [];
      if (!filterTypes.has('taxonomy')) filters.taxonomy = [];
      if (!filterTypes.has('hasExternal')) filters.hasExternal = '';
      if (!filterTypes.has('dateRange')) filters.dateRange = [];
      // rec_oid / prod_oid / order_oid / 排序為通用能力（非 schema-gated），切來源一律歸零避免誤帶
      filters.recOid = '';
      filters.prodOid = '';
      filters.orderOid = '';
      sortValue.value = 'occurred_at:desc';
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
    loadCascadeOptions();
    loadPage();
    loadUnjudged();
  };

  // ── 選取（跨頁累積；下沉 useAttributionSelection，注入 rows/pageSize/filterQuery；selectedKeys
  //    解構出來供 onFilterChange 清空 / exportCsv / usePrejudgeJob 直接用，維持原變數名不改呼叫端）──
  const selection = useAttributionSelection({ rows, pageSize, filterQuery });
  const { selectedKeys } = selection;

  // ── 初判歸因批次 + 單列重判（下沉 usePrejudgeJob；注入依賴，回傳 ref 保留 identity 不改綁定）──
  // 頁面列表篩選快照（scope 模式「套用當前列表篩選」用；與 filterQuery 同源值，鍵名對齊 getProblems）
  const listFilters = computed(() => filtersToParams(filters));
  const job = usePrejudgeJob({
    source,
    llmConfigId,
    effVerticals,
    selectedKeys,
    listFilters,
    reload: async () => {
      await loadPage();
      await loadUnjudged();
    },
  });

  // ── 導出（背景 job + SSE 實時進度 + 可停止）：改彈窗流程，開啟時草稿帶入列表當前篩選、可重選 ──
  const exportJob = useExportJob();
  const exportOpen = ref(false);
  /** 導出草稿篩選（與列表篩選同形狀；彈窗內可重選，不影響列表本身）。 */
  const exportFilters = reactive(emptyFilters());
  /** 開導出彈窗：草稿深拷貝列表當前篩選（有勾選時提示只導勾選列，篩選欄仍顯示以供參考）。 */
  const openExport = () => {
    Object.assign(exportFilters, cloneFilters(filters));
    exportOpen.value = true;
  };
  /** 確認導出：以草稿篩選啟動背景 job（歸因列表）；有勾選 review 則只導那些（item_ids 優先於篩選）。 */
  const doExport = () => {
    exportOpen.value = false;
    const p = filtersToParams(exportFilters);
    return exportJob.run(
      () =>
        startProblemsExport({
          source: toValue(source),
          product_verticals: effVerticals.value,
          polarity: p.polarity,
          stage: p.stage,
          confidence_tier: p.confidenceTier,
          status: p.status,
          taxonomy: p.taxonomy,
          has_external: p.hasExternal === undefined ? undefined : p.hasExternal === 'true',
          date_from: p.dateFrom,
          date_to: p.dateTo,
          rec_oid: p.recOid,
          prod_oid: p.prodOid,
          order_oid: p.orderOid,
          item_ids: selectedKeys.value.length ? selectedKeys.value : undefined,
        }),
      exportName('歸因列表', 'xlsx'),
    );
  };

  // ── 單列覆核（操作欄；與批量 selectedKeys 解耦；單列重判已下沉 usePrejudgeJob.rejudgeRow）──
  /** 覆核結果提示文案（confirmed/dismissed/new＝撤銷覆核回待處理）。 */
  const REVIEW_DONE_MSG: Record<string, string> = {
    confirmed: '已確認',
    dismissed: '已忽略',
    new: '已撤銷覆核',
  };
  /**
   * 單條歸因覆核：只改該 finding 的 status（per-attribution；每條歸因分開操作）。
   * optimistic 即時回寫（PATCH 秒級·無 loading）；只改人工 status 軸、不動 AI stage。
   * 再點選中狀態＝撤銷覆核（status='new'）；失敗回滾快照（避免 UI 與後端漂移）。
   */
  const reviewFinding = async (attr: Attribution, status: string) => {
    if (!attr.finding_id) return;
    const next = attr.status === status ? 'new' : status; // 再點選中態＝撤銷
    const prev = attr.status;
    attr.status = next; // optimistic：覆核徽章即時反映
    try {
      await patchStatus(attr.finding_id, next);
      Message.success(REVIEW_DONE_MSG[next] || next);
    } catch (e: any) {
      attr.status = prev; // 失敗回滾（樂觀值不留殘影）
      Message.error('覆核失敗：' + (e?.message || e));
    }
  };

  /**
   * 批量覆核：對已勾選評論（selectedKeys＝source_id）的**全部**歸因設定 status；
   * 後端單交易逐筆 diff（同值冪等跳過）並記入判決歷史。完成後重載列表並清空勾選。
   */
  const batchReview = async (status: string) => {
    if (!selectedKeys.value.length) return;
    try {
      const r = await batchPatchStatus(toValue(source), selectedKeys.value, status);
      Message.success(`${REVIEW_DONE_MSG[status] || status}：更新 ${r.updated} 條歸因`);
      selectedKeys.value = [];
      await loadPage();
    } catch (e: any) {
      Message.error('批量覆核失敗：' + (e?.message || e));
    }
  };

  return {
    schema,
    // 篩選（單一 reactive 物件；AttributionFilterBar 綁定）
    filters,
    cascadeOptions,
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
    // 單列覆核 + 批量覆核
    reviewFinding,
    batchReview,
    // 導出（彈窗草稿流程 + 型態選擇 + 背景 job + 實時進度 + 停止）
    exportOpen,
    exportFilters,
    openExport,
    doExport,
    exporting: exportJob.exporting,
    exportStatus: exportJob.status,
    exportProgress: exportJob.progress,
    exportPct: exportJob.pct,
    cancelExport: exportJob.cancel,
    // 初始化（onMounted 呼叫）
    init,
  };
}

<script setup lang="ts">
/**
 * 歸因列表（伺服器端分頁 + 選擇驅動初判歸因 + 正負傾向 + 原始+歸因合表）。
 *
 * 分頁/篩選/排序皆走後端（/api/problems limit-offset；occurred_at DESC 穩定）；表頭固定、表身內滾動、
 * 底部完整 Arco 分頁。選取跨頁累積（複選 / 分頁選取 / 全部未初判 scope）；導出走後端全量 CSV。
 * 正向/中性 不歸因，只有負向才有 L1→L2。
 *
 * 資料/篩選/選取/初判歸因/導出邏輯下沉 `useAttributionList`；欄位/篩選器與顯示差異化
 * （內容欄標籤/對話模式/關聯資料段落/精確查詢 placeholder）依來源讀 `SOURCE_LIST_SCHEMAS`：
 * reviews＝評論全文＋星等；conversations＝進線對話輪次（[ROLE]: 解析）＋進線屬性段。
 */
import { PERM } from '@/api';
import {
  CollapsibleSidePanel,
  ExportProgressBar,
  LlmConfigSelect,
  ScrollFadeArea,
  TableLayout,
} from '@/components';
import { usePermission } from '@/composables/usePermission';
import { fmtPercent } from '@/utils';
import { IconCode, IconDownload, IconEdit, IconEraser, IconEye, IconFile, IconHistory, IconMessage, IconPauseCircle, IconRobot, IconRotateLeft, IconSelectAll, IconSync, IconUndo } from '@arco-design/web-vue/es/icon';
import { computed, defineAsyncComponent, nextTick, onMounted, ref } from 'vue';
import {
  AttributionDetailDrawer,
  AttributionFilterBar,
  ExternalReviewPanel,
  PrejudgeLogView,
  PromptVersionPickerGroup,
  RecordContextPanel,
} from '../components';
import { useAttributionCorrection, useAttributionList, useRejudgeConfirm } from '../composables';
import {
  CONF_TIER_CLASS,
  DIALOGUE_ROLE_COLORS,
  DIALOGUE_ROLE_LABELS,
  DIALOGUE_SEGMENT_LABELS,
  idPlaceholderFor,
  POLARITY_COLOR,
  POLARITY_LABELS,
  REVIEW_STATUS_LABELS,
  SECTION_LABEL_CLASS,
  SOURCES,
  STAGE_COLOR,
  STAGE_LABELS,
  TIER_LABELS,
  type FilterField,
  type ProblemRow,
  type TimelineScope,
} from '../constants';
import { fmtDt, formatActor, parseDialogue, sentimentClass, type DialogueTurn } from '../utils';
import { notifyComingSoon } from '@/utils';

const SOURCE_OPTS = SOURCES.map((s) => ({ value: s.value, label: s.label }));

/** 判決歸因佔位文案：說清楚它是什麼、現在可以先做什麼（不只是「還沒做」）。 */
const VERDICT_COMING_SOON =
  '判決歸因會在初判分類之上判定「責任方 · 嚴重度 · 建議行動」，讓質檢結果能追到供應商／商品／客服。'
  + '資料欄位與值域主檔已就緒（設定 › 判決值域可維護），判定流程開發中。'
  + '目前可先用「人工糾正」修正 AI 的分類。';

/** 判決歷史佔位文案：說清楚「軸只有一條」，現在就能從人工紀錄看到跨階段的完整脈絡。 */
const VERDICT_HISTORY_COMING_SOON =
  '判決事件（定責 · 嚴重度 · 建議行動的變更）尚未產生，因此這一段還是空的。'
  + '時間軸本身只有一條——判決功能上線後，判決事件會自動排進同一條軸，'
  + '現在可先從「人工歷史」看到糾正、複審與備註的完整脈絡。';

// 按鈕級權限遮罩（後端 403 兜底；此處 disabled 讓無權者一眼可辨「功能存在但不可用」）
const { can } = usePermission();
const canPrejudge = computed(() => can(PERM.prejudgeRun));
const canExport = computed(() => can(PERM.problemListExport));
const canCorrect = computed(() => can(PERM.attributionCorrect));
// 「確認正確」屬複審域（attribution.review），與糾正是**不同的**權限鍵——工作台裡兩種動作並排，
// 共用一個旗標會讓只有其中一種權限的人看到錯誤的可用狀態。
const canReview = computed(() => can(PERM.attributionReview));

// 歸因歷史抽屜（點開才載；每次批量/選取/單筆重新初判的 LLM 使用紀錄）
const PrejudgeRunsDrawer = defineAsyncComponent(
  () => import('../components/PrejudgeRunsDrawer.vue'),
);
// 終態摘要卡「查看 LLM 日誌」目標（點開才載；歷史快照回看專用，大批量無快照時元件自帶說明）
const PrejudgeLogDrawer = defineAsyncComponent(() => import('../components/PrejudgeLogDrawer.vue'));

// ── 人工介入（糾正 / 待審建議）：點開才載，兩個抽屜都不進首屏 ──
const AttributionCorrectionDrawer = defineAsyncComponent(
  () => import('../components/AttributionCorrectionDrawer.vue'),
);
const AttributionSuggestionDrawer = defineAsyncComponent(
  () => import('../components/AttributionSuggestionDrawer.vue'),
);
/** 糾正完成後重載列表（現值變了、徽記可能出現或消失）。 */
const correction = useAttributionCorrection(() => void loadPage());
const suggestionOpen = ref(false);
const suggestionTarget = ref<{ source: string; sourceId: string }>({ source: '', sourceId: '' });

/**
 * 開人工糾正工作台。
 *
 * **只傳反饋座標，不預選任何一條歸因**——工作台是反饋級的，開啟後列出該反饋的全部歸因
 * （含已標記誤判的），使用者自己挑要動哪一條。舊版在這裡取 `attributions[0]` 當預設標的，
 * 於是一則反饋有多條歸因時第二條以後完全碰不到。
 */
const openCorrection = (record: ProblemRow) => {
  void correction.openFor(source.value, String(record.source_id ?? record._group ?? ''));
};

/** 開待審建議對比抽屜（以反饋 id）。 */
const openSuggestionsFor = (sourceId: string) => {
  if (!sourceId) return;
  suggestionTarget.value = { source: source.value, sourceId };
  correction.open.value = false; // 兩個抽屜不疊層：跳過去時先收掉工作台
  suggestionOpen.value = true;
};

/** 開待審建議對比抽屜（列表列徽記入口）。 */
const openSuggestions = (record: ProblemRow) => {
  openSuggestionsFor(String(record.source_id ?? record._group ?? ''));
};
const logDrawerVisible = ref(false);
const logDrawerJobId = ref('');
/** 終態摘要卡「查看 LLM 日誌」：以 lastRun.jobId 精準開啟該次 job 的日誌快照。 */
const openLastRunLog = () => {
  if (!lastRun.value) return;
  logDrawerJobId.value = lastRun.value.jobId;
  logDrawerVisible.value = true;
};
const runsDrawerVisible = ref(false);

// 歸因歷史抽屜（評論級時間軸：初判快照/判決轉移/備註；點開才載）
const AttributionHistoryDrawer = defineAsyncComponent(
  () => import('../components/AttributionHistoryDrawer.vue'),
);
const historyOpen = ref(false);
const historyRow = ref<ProblemRow | null>(null);
const historyScope = ref<TimelineScope>('all');
/**
 * 開某則反饋的時間軸（source_id 級；與 run 級「歸因歷史」抽屜不同層）。
 *
 * `scope` 切的是「看哪一段」——**一則反饋只有一條時間軸**，初判／判決／人工三個入口開的是
 * 同一條軸的不同視圖，不是三條各自獨立的歷史（理由見 constants/timeline.constant.ts）。
 */
const openTimeline = (record: ProblemRow, scope: TimelineScope = 'all') => {
  historyRow.value = record;
  historyScope.value = scope;
  historyOpen.value = true;
};

const source = ref('reviews');

const {
  schema,
  filters,
  cascadeOptions,
  modelOptions,
  verticalOptions,
  verticalGroups,
  onVerticalChange,
  onSortChange,
  onFilterChange,
  activeFilterCount,
  resetFilters,
  llmConfigId,
  llmConfigs,
  llmProviderHasToken,
  llmOverrides,
  rows,
  total,
  unjudged,
  page,
  pageSize,
  loading,
  error,
  loadPage,
  selectedRowKeys,
  onSelectionChange,
  runCount,
  clearSelection,
  pageSpec,
  selectPages,
  running,
  jobStatus,
  progress,
  progressPct,
  costText,
  logEntries,
  logStreaming,
  logError,
  lastRun,
  failedItems,
  failedTruncated,
  retryFailed,
  confirmOpen,
  openPrejudge,
  targetMode,
  targetStages,
  lowConfOnly,
  draftFilters,
  targetCount,
  hasJudgedStage,
  refreshTargetCount,
  doRun,
  pauseJob,
  resumeJob,
  cancelJob,
  exportOpen,
  exportFilters,
  exportSnapshotModel,
  exportCompareModels,
  openExport,
  doExport,
  exporting,
  exportStatus,
  exportProgress,
  exportPct,
  cancelExport,
  isRowBusy,
  rejudgeRow,
  init,
} = useAttributionList(source);

// 單列重新初判完成 + 重載後，把表身捲回剛判的那一列（大列表·表身內滾動 y='100%'，重載會回頂 → 失去位置）。
// ref 掛在 TableLayout（內建表格模式），內部 a-table 實例經其 expose 的 tableRef 取得。
const tableRef = ref<{ tableRef?: { $el: HTMLElement } | null } | null>(null);
const onRejudge = async (id: string, promptVersions?: Record<string, number>) => {
  // composable 內含 SSE 等待 + 重載本頁（同頁碼/排序 → 該列索引不變）；執行日誌（logEntries/
  // logStreaming）由 usePrejudgeJob 內部直接開流，就地顯示於確認抽屜本身，不再另開獨立抽屜。
  await rejudgeRow(id, promptVersions);
  await nextTick();
  const idx = rows.value.findIndex((r) => String(r._group) === id);
  if (idx < 0) return;
  const tr = tableRef.value?.tableRef?.$el?.querySelectorAll('.arco-table-body tbody > tr')[idx];
  (tr as HTMLElement | undefined)?.scrollIntoView({
    block: 'center',
    behavior: 'auto',
  }); // 即時定位，無滾動動畫
};

// ── 確認初判分類抽屜：批量（工具列）與單列（操作欄）共用同一個 confirmOpen 抽屜，
//    狀態計算/決策邏輯下沉 useRejudgeConfirm；template 結構（CollapsibleSidePanel 等）留在本檔 ──
const {
  confirmScope,
  confirmRowId,
  confirmSettingsOpen,
  confirmVersionSelection,
  openRowConfirm,
  openBatchConfirm,
  onConfirmRun,
  confirmModelLabel,
  confirmPinnedVersions,
  rejudgeConfirmText,
} = useRejudgeConfirm({
  confirmOpen,
  logEntries,
  logError,
  lastRun,
  llmOverrides,
  runRejudgeRow: onRejudge,
  runBatch: doRun,
  openBatchTargeting: openPrejudge,
});

// ── 操作：查看歸因詳情抽屜（純前端，資料取自該列 attributions）──
const detailRow = ref<ProblemRow | null>(null);
const detailOpen = ref(false);
/** 開查看詳情抽屜。 */
const viewDetail = (record: ProblemRow) => {
  detailRow.value = record;
  detailOpen.value = true;
};
/** 信心數字按分層上色（CONF_TIER_CLASS 見 constants/pipeline.constant；未知 tier 回預設文字色）。 */
const confClass = (tier?: string): string =>
  CONF_TIER_CLASS[tier || ''] || 'text-[var(--color-text-1)]';

/** 反饋內容欄「補充」區塊是否有內容：外部評論融合維度有值，或該來源有 supplementSections 段落
 *  （如進線屬性）。兩者皆無的列整塊不顯示，避免空區塊佔列高與多一條分隔線。 */
const hasSupplement = (record: ProblemRow): boolean =>
  !!record.ext_sentiment ||
  !!(record.ext_free_tag as unknown[] | undefined)?.length ||
  schema.value.supplementSections.length > 0;

// ── 來源顯示差異化（schema 驅動：內容標籤/對話模式/關聯段落/精確查詢 placeholder）──
// 關聯資料欄的段落顯示（hasSection）已隨模板一併下沉 RecordContextPanel 元件（見 #context slot）。
/** 內容欄對話輪次：dialogue 模式且解析出 [ROLE]: 前綴才回輪次；否則 null → 原樣全文 fallback。 */
const dialogueTurns = (record: ProblemRow): DialogueTurn[] | null =>
  schema.value.contentMode === 'dialogue' ? parseDialogue(String(record.content || '')) : null;
/** 該輪是否為新段落起點（首輪或與前一輪段落不同）：機器人／真人客服階段切換時插入分隔標籤。 */
const isNewSegment = (turns: DialogueTurn[], idx: number): boolean =>
  idx === 0 || turns[idx - 1].segment !== turns[idx].segment;
/** 精確查詢 placeholder（隨來源切換：評論 rec_oid／進線 session_oid…，與後端 natural_key 篩選對齊）。 */
const idPlaceholder = computed(() => idPlaceholderFor(source.value));

/** schema filter type → AttributionFilters 欄位鍵（現皆同名，保留映射以隔離 schema 命名）。 */
const SCHEMA_TO_FIELD: Record<string, FilterField> = {
  polarity: 'polarity',
  stage: 'stage',
  tier: 'tier',
  model: 'model',
  taxonomy: 'taxonomy',
  hasExternal: 'hasExternal',
  dateRange: 'dateRange',
  bucket: 'bucket',
};
/** 工具列篩選欄位：schema 決定的維度 + 通用精確查詢（rec/prod/order id 恆顯示）。 */
const toolbarFields = computed<FilterField[]>(() => {
  const fromSchema = schema.value.filters
    .map((f) => SCHEMA_TO_FIELD[f.type])
    .filter((k): k is FilterField => Boolean(k));
  return [...fromSchema, 'recOid', 'prodOid', 'orderOid'];
});
/** 初判彈窗「目標篩選」欄位：統一完整篩選欄（與列表對齊）。第一行 id/日期，第二行 傾向/信心分層/歸因分類/外部評論。
 *  日期/id/外部評論 為表級（兩分支皆套）；傾向/信心分層/歸因分類 為初判級（只對已初判分支生效，見 _scopeBody
 *  的 hasJudgedStage 閘）。初判階段由上方 checkbox 承擔 → 不納入此篩選欄。 */
const PREJUDGE_TARGET_FIELDS: FilterField[] = [
  'recOid',
  'prodOid',
  'orderOid',
  'dateRange',
  'polarity',
  'tier',
  'taxonomy',
  'hasExternal',
];

/** 序號欄（前置於業務欄前）：依當前頁碼 + 列索引計算全域序號。 */
const SEQ_COL = { title: '序號', slotName: 'seq', width: 64 };
/** 目前來源欄位（序號欄 + schema 業務欄）。 */
const COLS = computed(() => [SEQ_COL, ...schema.value.columns]);
/** 表格水平捲動總寬（欄寬合計 + selection 欄），欄多時橫向捲動不擠壓內容。 */
const SCROLL_X = computed(() => COLS.value.reduce((w, c) => w + (Number(c.width) || 120), 0) + 48);

onMounted(init);
</script>

<template>
  <!--
    反饋來源＝切換「看哪一份資料」（與歸因概覽的檢視切換同一份 SSOT：SOURCES），語義是導航
    不是篩選，故送進主 tab 列下方的子 tab 列，並與歸因概覽採同一種控件（分段按鈕）。
  -->
  <Teleport to="#page-subtabs">
    <a-radio-group v-model="source" type="button" size="small" @change="onFilterChange">
      <a-radio v-for="s in SOURCE_OPTS" :key="s.value" :value="s.value">{{ s.label }}</a-radio>
    </a-radio-group>
  </Teleport>

  <!-- 初判歸因控制列送進固定工具列橫帶（子 tab 列下方），與歸因概覽一致、恆常可見 -->
  <Teleport to="#page-toolbar">
    <div class="flex items-center gap-3">
      <!-- 商品垂直分類複選（全局 SSOT；預設不篩選，勾選才收斂範圍；順序於「配置」規則頁拖曳調整）-->
      <span class="text-sm text-gray-500">商品垂直分類</span>
      <a-select
        :model-value="verticalGroups"
        multiple
        size="small"
        style="width: 220px"
        :max-tag-count="1"
        placeholder="全部（未篩選）"
        :options="verticalOptions.map((g) => ({ value: g, label: g }))"
        @change="onVerticalChange"
      />
      <!-- 歸因模型選擇已移進「確認初判分類」抽屜（本次執行才需要選，見 LlmConfigSelect）-->
      <!-- 統一操作區：三顆全填滿、靠色相分主次，聚合成一條 button-group（見 rules/frontend-vue.md「同類按鈕聚合」）。
           順序＝兩顆「初判*」相鄰成族、導出殿後：
           初判分類 primary(藍·主行為) → 初判歷史 secondary(灰·檢視) → 導出列表 primary+success(綠·導出)。 -->
      <a-button-group size="small">
        <a-button
          type="primary"
          :loading="running"
          :disabled="!canPrejudge"
          @click="openBatchConfirm"
        >
          <!-- icon 與列內「初判分類」同一個（IconRobot＝AI 產出的判定）：批量與單列是同一個動作
               的兩種範圍，用不同 icon 會讓人以為是兩件事 -->
          <template #icon><icon-robot /></template>
          初判分類{{ runCount ? `（已選 ${runCount}）` : '' }}
        </a-button>
        <!-- 初判執行紀錄：純檢視（每次批量/選取/單筆重新初判的 LLM 使用紀錄）。
             **不叫「初判歷史」**——那是列操作欄裡「某則反饋的初判事件時間軸」的名字，兩者層級不同，
             同名會讓人以為開錯視窗（見 rules/frontend-vue.md「抽屜/彈窗標題命名」第 4 條）。
             在 button-group 內用 secondary（有底色）而非 text——text 無邊框會讓群組看起來不相連，
             見 rules/frontend-vue.md「同類按鈕聚合」對 group 內禁用 text 的說明 -->
        <a-button type="secondary" @click="runsDrawerVisible = true">
          <template #icon><icon-history /></template>
          初判執行紀錄
        </a-button>
        <a-button
          type="primary"
          status="success"
          :loading="exporting"
          :disabled="!canExport"
          @click="openExport"
        >
          <template #icon><icon-download /></template>
          導出列表{{ runCount ? `（已選 ${runCount}）` : '' }}
        </a-button>
      </a-button-group>
    </div>
  </Teleport>

  <!-- 歸因歷史抽屜（懶載；unmount-on-close）-->
  <PrejudgeRunsDrawer v-model:visible="runsDrawerVisible" />

  <!-- 人工糾正抽屜（修改 / 新增 / 標記誤判三模式共用；狀態由 useAttributionCorrection 持有）-->
  <!-- 人工糾正工作台（反饋級）。`@suggestions` 讓工作台頂部的橫幅能跳到待審建議抽屜——
       兩個入口操作同一份資料，必須互相看得見。 -->
  <AttributionCorrectionDrawer
      :ctl="correction"
      :can-review="canReview"
      @suggestions="openSuggestionsFor(correction.target.sourceId)"
    />

  <!-- 待審建議對比抽屜（人工現值 vs LLM 新值；採納後重載列表讓徽記與現值同步）-->
  <AttributionSuggestionDrawer
    v-model:visible="suggestionOpen"
    :source="suggestionTarget.source"
    :source-id="suggestionTarget.sourceId"
    @resolved="loadPage"
  />

  <!-- 終態摘要卡「查看 LLM 日誌」目標（歷史快照回看；懶載）-->
  <PrejudgeLogDrawer v-model:visible="logDrawerVisible" :job-id="logDrawerJobId" />

  <!-- 反饋級時間軸抽屜（依 scope 切階段視圖；懶載）-->
  <AttributionHistoryDrawer
      v-model:visible="historyOpen"
      :source="source"
      :row="historyRow"
      :scope="historyScope"
    />

  <div class="flex h-full flex-col gap-4">
    <!-- 本批失敗筆：初判完成後（非執行中）有失敗才顯示——可查原因 + 一鍵重新初判（走 item_ids 顯式路徑）-->
    <a-alert v-if="!running && failedItems.length" type="warning" class="flex-none">
      <template #title>
        本批 {{ failedItems.length
        }}{{ failedTruncated ? '+' : '' }} 筆初判失敗（未落庫、等同未初判）
      </template>
      <div class="flex flex-wrap items-center gap-3">
        <span class="text-xs text-[#86909c]"
          >失敗筆可重新初判補上；系統性失敗連續多次後會停止隱式重撈，需在此手動重新初判。</span
        >
        <a-popover position="bl">
          <a-button size="mini" type="text"><template #icon><icon-eye /></template>查看原因</a-button>
          <template #content>
            <ScrollFadeArea max-height="16rem" class="w-96 text-xs">
              <div v-for="f in failedItems" :key="f.item_id" class="mb-1 break-all">
                <span class="text-[#86909c]">{{ f.source_id || f.item_id }}</span
                >：{{ f.error }}
              </div>
            </ScrollFadeArea>
          </template>
        </a-popover>
        <a-button size="mini" type="primary" status="warning" @click="retryFailed"
          ><template #icon><icon-sync /></template>重新初判本批失敗筆</a-button
        >
      </div>
    </a-alert>
    <!-- 導出實時進度：導出進行中才顯示（背景 job + SSE，可停止）-->
    <ExportProgressBar
      v-if="exporting"
      label="導出列表"
      :status="exportStatus"
      :processed="exportProgress.processed"
      :total="exportProgress.total"
      :pct="exportPct"
      @cancel="cancelExport"
    />

    <TableLayout
      ref="tableRef"
      v-model:page="page"
      v-model:page-size="pageSize"
      :title="`歸因列表（共 ${total} · 未初判 ${unjudged}）`"
      hint="伺服器端分頁；勾選/分頁選取做初判分類或導出"
      :data="rows"
      :columns="COLS"
      :loading="loading"
      :error="error"
      empty-text="尚無資料，請先到「資料上傳」上傳 CSV"
      server
      :total="total"
      :row-selection="{
        type: 'checkbox',
        selectedRowKeys,
        showCheckedAll: true,
      }"
      row-key="_group"
      :scroll="{ x: SCROLL_X }"
      @change="loadPage"
      @selection-change="onSelectionChange"
      @sorter-change="onSortChange"
    >
      <template #toolbar>
        <!-- 篩選維度列：共用 AttributionFilterBar（單一真相；新增/調整篩選改元件一處即三處生效）。
             fields 依 schema 動態決定（各來源可篩欄不同），rec/prod/order id 為通用能力恆顯示。 -->
        <AttributionFilterBar
          :model="filters"
          :fields="toolbarFields"
          :cascade-options="cascadeOptions"
          :model-options="modelOptions"
          :id-placeholder="idPlaceholder"
          class="mb-2"
          @change="onFilterChange"
        />

        <!-- 分頁選取 + 操作（右側 flex=auto 撐開，計數與重置靠右）-->
        <a-row :gutter="[8, 8]" align="center">
          <a-col flex="190px">
            <a-input
              v-model="pageSpec"
              size="small"
              allow-clear
              class="w-full"
              placeholder="分頁選取 如 1,2~5"
              @press-enter="selectPages"
            />
          </a-col>
          <a-col flex="none">
            <a-button size="small" type="outline" @click="selectPages">
              <template #icon><icon-select-all /></template>
              選取分頁
            </a-button>
          </a-col>
          <a-col flex="none">
            <!-- 常駐可見以利發現「取消選擇」；無選取時 disabled（非 v-if 隱藏） -->
            <a-button size="small" :disabled="!runCount" @click="clearSelection">
              <template #icon><icon-eraser /></template>
              清除選擇
            </a-button>
          </a-col>
          <a-col flex="auto" class="flex items-center justify-end gap-2">
            <span v-if="activeFilterCount" class="text-xs text-[rgb(var(--primary-6))]">
              已套用 {{ activeFilterCount }} 項篩選
            </span>
            <span class="text-xs text-gray-400">每頁 {{ pageSize }} · 已選 {{ runCount }}</span>
            <a-button size="small" type="outline" status="warning" @click="resetFilters">
              <template #icon><icon-rotate-left /></template>
              重置篩選
            </a-button>
          </a-col>
        </a-row>
      </template>
      <template #seq="{ record }">{{ record._seq }}</template>
      <!-- 反饋內容欄：兩區塊，標籤跨來源統一為「原文」/「補充」（不按來源改名，否則同一欄在商品評論
           /進線間出現四種名稱；統一兩字使左側標籤欄等寬對齊）——①原文＝該則反饋的內容本體（評論
           全文或進線對話輪次）②補充＝關於這則反饋自身的附加屬性（外部評論融合維度 +
           schema.supplementSections 段落，如進線的分桶/行程階段/處理方/訊息數）。 -->
      <template #review="{ record }">
        <div class="flex flex-col gap-1">
          <!-- ① 原文（星等/標題僅評論形來源有值；進線走對話輪次渲染）-->
          <div class="flex gap-1.5">
            <span :class="SECTION_LABEL_CLASS">原文</span>
            <div class="min-w-0">
              <div class="mb-0.5 flex flex-wrap items-center gap-x-2 gap-y-1">
                <a-rate
                  v-if="record.score !== null && record.score !== undefined && record.score !== ''"
                  :model-value="Number(record.score) || 0"
                  readonly
                  :count="5"
                  class="review-rate"
                />
                <!-- 傾向類別標籤（正向/中性/負向；驅動歸因）-->
                <a-tag
                  v-if="record.polarity"
                  size="small"
                  :color="POLARITY_COLOR[String(record.polarity)]"
                >
                  {{ POLARITY_LABELS[String(record.polarity)] || record.polarity }}
                </a-tag>
                <!--
                  沒有傾向時要分兩種情況講清楚：判過但歸因全被標記為 AI 誤判（dismissed），
                  與從未判過（unjudged）。兩者都是空白，但意義相反——把 dismissed 顯示成
                  「未初判」會與批量初判的標的數對不起來（它認為這則判過、不會再撈）。
                  判定狀態一律讀服務端派生的 judge_state，不用 polarity/attributions 是否存在推斷。
                -->
                <span
                  v-else-if="record.judge_state === 'dismissed'"
                  class="text-xs text-gray-400"
                  :title="`AI 的歸因已全部被人工標記為誤判（${record.dismissed_count ?? 0} 條），可在人工糾正中還原`"
                >
                  已標記誤判
                </span>
                <span v-else class="text-xs text-gray-300">未初判</span>
                <!-- 我方情緒分 1-5（重新初判後回填；與外部評論情緒分同尺度直接對比）-->
                <span v-if="record.our_sentiment" class="flex items-center gap-1 text-xs">
                  <span class="text-[var(--color-text-3)]">情緒分:</span>
                  <span class="font-semibold" :class="sentimentClass(record.our_sentiment)">
                    {{ record.our_sentiment }}/5
                  </span>
                </span>
                <span v-if="record.title" class="text-sm font-medium text-[var(--color-text-1)]">
                  {{ record.title }}
                </span>
              </div>
              <!-- 進線對話：按 [ROLE]: 前綴解析輪次（角色 tag + 該輪文字），一眼辨發話方；
                   非對話模式或解析失敗 fallback 原樣全文。內容區固定高度內滾動，避免長對話/長文
                   把整列列高撐爆（列高改由關聯資料/判決歸因等其他欄決定），完整內容仍可從「查看詳情」抽屜看。
                   捲動區走 ScrollFadeArea：底部漸隱＋提示，避免使用者誤以為內容到此為止（捲到底自動消失）。 -->
              <template v-if="record.content">
                <ScrollFadeArea v-if="dialogueTurns(record)" max-height="10rem">
                  <div class="flex flex-col gap-1">
                    <template v-for="(t, ti) in dialogueTurns(record)" :key="ti">
                      <!-- 段落分隔：機器人／真人客服階段切換時插入標籤（對齊 conversation_full 的 ‖ 分段）-->
                      <div
                        v-if="t.segment && isNewSegment(dialogueTurns(record) || [], ti)"
                        class="mt-1 text-[10px] font-semibold text-[var(--color-text-3)]"
                      >
                        {{ DIALOGUE_SEGMENT_LABELS[t.segment] || t.segment }}
                      </div>
                      <div class="text-xs leading-relaxed">
                        <a-tag
                          v-if="t.role"
                          size="small"
                          :color="DIALOGUE_ROLE_COLORS[t.role] || 'gray'"
                          class="mr-1"
                          >{{ DIALOGUE_ROLE_LABELS[t.role] || t.role }}</a-tag
                        >
                        <span class="whitespace-pre-wrap text-[var(--color-text-2)]">{{
                          t.text
                        }}</span>
                      </div>
                    </template>
                  </div>
                </ScrollFadeArea>
                <ScrollFadeArea v-else max-height="10rem">
                  <div
                    class="whitespace-pre-wrap text-xs leading-relaxed text-[var(--color-text-2)]"
                  >
                    {{ record.content }}
                  </div>
                </ScrollFadeArea>
              </template>
              <div class="mt-0.5 text-[11px] text-[var(--color-text-3)]">
                #{{ record.source_record_id || record.source_id || '—' }} ·
                {{ fmtDt(record.occurred_at) || '—' }}
              </div>
            </div>
          </div>
          <!-- ② 補充：supplementSections 段落（conversations 的進線屬性）+ 外部評論融合維度
               （reviews）；共用單一外層標籤，內部各段不再各自帶標籤（showLabels=false），
               兩者皆無值的列整塊不顯示 -->
          <div
            v-if="hasSupplement(record)"
            class="flex gap-1.5 border-t border-[var(--color-border-1)] pt-1"
          >
            <span :class="SECTION_LABEL_CLASS">補充</span>
            <div class="flex min-w-0 flex-col gap-1">
              <!-- 進線等「這則反饋自身的屬性」段落，複用 RecordContextPanel 同一份渲染 -->
              <RecordContextPanel
                v-if="schema.supplementSections.length"
                :record="record"
                :sections="schema.supplementSections"
                :show-labels="false"
              />
              <!-- 外部評論（評論系統 LLM 標籤；無融合資料的列不渲染）——與詳情抽屜共用同一元件 -->
              <ExternalReviewPanel
                v-if="record.ext_sentiment || record.ext_free_tag?.length"
                :record="record"
              />
            </div>
          </div>
        </div>
      </template>
      <!-- 判決歸因合併欄：每條歸因一塊（L1→L2 + 信心 + 分層 + 初判階段 全放一起），
               塊間細線分隔；多歸因並存時逐塊堆疊，資訊聚合、一眼看完整初判。 -->
      <template #verdict="{ record }">
        <!-- 需要注意的標記集中在這一欄的頂部：待審建議（AI 有話說）與備註數（人留過話）。
             ⚠️ 備註徽記**不是裝飾**：功能沒有可見性就沒人用——2026-08-04 退役的人工判決軸
             正是這樣死的（6,242 條裡只有 1 個人按過那兩顆按鈕）。 -->
        <div v-if="record.suggestion_count || record.note_count" class="mb-1 flex flex-wrap gap-1">
          <a-tag
            v-if="record.suggestion_count"
            size="small"
            color="red"
            class="cursor-pointer"
            @click="openSuggestions(record)"
          >
            AI 有 {{ record.suggestion_count }} 條新建議
          </a-tag>
          <a-tag
            v-if="record.note_count"
            size="small"
            color="gray"
            class="cursor-pointer"
            @click="openTimeline(record, 'human')"
          >
            <template #icon><icon-message /></template>
            {{ record.note_count }} 則備註
          </a-tag>
        </div>
        <template v-if="record.attributions && record.attributions.length">
          <!-- 每條歸因一塊，比照關聯資料欄：左小標籤（摘要/歸因/信心/操作）+ 右內容或操作 -->
          <div
            v-for="(a, ai) in record.attributions"
            :key="ai"
            class="verdict-blk flex flex-col gap-1 text-xs leading-relaxed"
          >
            <!-- 摘要（LLM 繁中概括，顯明；僅有值才顯示）-->
            <div v-if="a.content?.summary" class="flex gap-1.5">
              <span :class="SECTION_LABEL_CLASS">摘要</span>
              <div class="min-w-0 font-medium leading-snug text-[var(--color-text-1)]">
                {{ a.content.summary }}
              </div>
            </div>
            <!-- 歸因（L1→L2 麵包屑）-->
            <div class="flex gap-1.5">
              <span :class="SECTION_LABEL_CLASS">歸因</span>
              <div class="min-w-0">
                <template v-if="[a.l1?.label, a.l2?.label].some(Boolean)">
                  <template
                    v-for="(lvl, li) in [a.l1?.label, a.l2?.label].filter(Boolean)"
                    :key="li"
                  >
                    <span v-if="li > 0" class="mx-1 text-[var(--color-text-3)]">›</span>
                    <span
                      :class="
                        li === 0
                          ? 'font-medium text-[rgb(var(--primary-6))]'
                          : 'text-[var(--color-text-2)]'
                      "
                    >
                      {{ lvl }}
                    </span>
                  </template>
                </template>
                <span v-else class="text-[var(--color-text-3)]">未歸因</span>
              </div>
            </div>
            <!-- 信心（值 + 分層 + 初判模型；stage 僅異常態顯示——三軸標籤收斂：status 移操作列）-->
            <div class="flex gap-1.5">
              <span :class="SECTION_LABEL_CLASS">信心</span>
              <div class="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
                <!-- 信心按 tier 上色：綠可採信 / 琥珀需複審 / 紅必人工（< 0.8 需人工判決）-->
                <span class="font-semibold" :class="confClass(a.confidence?.tier)">
                  {{
                    typeof a.confidence?.value === 'number' ? a.confidence.value.toFixed(2) : '—'
                  }}
                </span>
                <span
                  class="rounded bg-[var(--color-fill-2)] px-1.5 py-0.5 text-[var(--color-text-2)]"
                >
                  {{
                    a.confidence?.tier ? TIER_LABELS[a.confidence.tier] || a.confidence.tier : '—'
                  }}
                </span>
                <!-- 現值來源：人工糾正過的列顯示修改者取代初判模型（origin 由後端派生，前端不判斷）-->
                <a-tag
                  v-if="a.origin === 'human'"
                  size="small"
                  color="orange"
                  :title="a.correction_reason || ''"
                >
                  人工 · {{ formatActor(a.corrected_by) }}
                </a-tag>
                <a-tag v-else-if="a.model" size="small" color="purple">{{ a.model }}</a-tag>
                <!-- 複審狀態：只在非預設態顯示（未複審是常態，不佔位）-->
                <a-tag
                  v-if="a.review_status && a.review_status !== 'unreviewed'"
                  size="small"
                  :color="a.review_status === 'confirmed' ? 'green' : 'orange'"
                >
                  {{ REVIEW_STATUS_LABELS[a.review_status] || a.review_status }}
                </a-tag>
                <!-- 初判階段：僅非 judged 的異常態才提示（已初判＝常態不佔位；全量三軸見詳情抽屜）-->
                <a-tag
                  v-if="a.stage && a.stage !== 'judged'"
                  size="small"
                  :color="STAGE_COLOR[a.stage]"
                >
                  {{ STAGE_LABELS[a.stage] || a.stage }}
                </a-tag>
              </div>
            </div>
          </div>
        </template>
        <span v-else class="text-gray-300">—</span>
      </template>
      <!-- 關聯資料合併欄：訂單 → 商品 → 方案 → 供應商 → 旅客（源數據），各段左側小標籤 + 右側內容；
           渲染邏輯已抽為共用元件 RecordContextPanel（商品評論／售前售後進線／其餘反饋來源共用同一份，
           段落依 schema.contextSections 裁剪，不再各自維護一份模板）。 -->
      <template #context="{ record }">
        <RecordContextPanel :record="record" :sections="schema.contextSections" />
      </template>
      <!-- 操作欄：**按流程階段分組**（2026-08-07 重整）。
           原本五顆按鈕平鋪，每加一個功能就多一條，很快會變成一面看不出主從的連結牆。改成
           「階段標籤 + 該階段的動作」之後，功能歸屬一眼可辨，日後補判決／備註也有明確的落點。

           三個階段對齊系統的流水線：**初判**（AI 分類）→ **判決**（定責＋行動，未實作）→
           **人工**（糾正／備註，跨階段）。「查看詳情」不屬於任何階段，單獨置頂。

           動作標籤刻意只留動詞（「分類」而非「初判分類」）——階段前綴已由左側標籤承擔，重複寫
           會在當時 132px 的欄寬裡擠成兩行（該欄現為 180，見 source-schema.constant.ts）。

           每列都會重複這組按鈕，統一用 type="text" 輕量呈現（不套用 rules/frontend-vue.md
           「視覺區分主次」的 primary/outline/dashed 分級——那條規則鎖定 toolbar/卡片動作列/彈窗
           footer 這種「該區只出現一次」的場景；per-row 操作欄會隨列數重複出現，用色塊反而視覺噪音）。 -->
      <template #actions="{ record }">
        <!-- 組間距（gap-2）刻意大於組內間距（.act-group 的 4px）：換行堆疊成七行時，
             分組全靠這個間距層級表達（不再有「·」分隔點——它在並排時是多餘裝飾、
             換行時會變成行尾的孤兒字元）。 -->
        <div class="flex flex-col items-start gap-2">
          <!-- ① 反饋：這則資料本身（不是流程階段）。未初判亦可查看，原文/關聯資料恆常可看。 -->
          <div class="act-group">
                        <a-button class="!px-0" size="mini" type="text" @click="viewDetail(record)">
              <template #icon><icon-file /></template>
              反饋詳情
            </a-button>
          </div>

          <!-- ② 初判：AI 把反饋分類到 L1/L2 面向 -->
          <div class="act-group">
                        <!-- 點擊直接開「確認初判分類」抽屜（模型/版本選擇+額度提示），不用小 popconfirm
                 ——本次執行前要確認的設定已不只是「要不要覆寫」。 -->
            <a-button
              class="!px-0"
              type="text"
              size="mini"
              :loading="isRowBusy(record._group)"
              :disabled="!canPrejudge"
              @click="openRowConfirm(record)"
            >
              <template #icon><icon-robot /></template>
              初判分類
            </a-button>
            <a-button class="!px-0" size="mini" type="text" @click="openTimeline(record, 'prejudge')">
              <template #icon><icon-history /></template>
              初判歷史
            </a-button>
          </div>

          <!-- ③ 判決：在分類之上判定「責任方 · 嚴重度 · 建議行動」。兩者皆未實作，
               點擊給明確說明而非死按鈕（見 comingSoon.util 的三條配套規則）。 -->
          <div class="act-group">
                        <a-button
              class="!px-0"
              type="text"
              size="mini"
              @click="notifyComingSoon('判決歸因', VERDICT_COMING_SOON)"
            >
              <template #icon><icon-robot /></template>
              判決歸因
            </a-button>
            <a-button
              class="!px-0"
              type="text"
              size="mini"
              @click="notifyComingSoon('判決歷史', VERDICT_HISTORY_COMING_SOON)"
            >
              <template #icon><icon-history /></template>
              判決歷史
            </a-button>
          </div>

          <!-- ④ 人工：跨階段的人為介入。糾正改的是現值、備註留的是處理脈絡。 -->
          <div class="act-group">
                        <!-- 糾正過後該則反饋進入人工託管，重新初判不再覆蓋現值（改走待審建議），
                 故文案用「糾正」而非「編輯」。
                 ⚠️ **恆常顯示，不以 attributions.length 隱藏**（2026-08-07 修）：歸因全被標記為
                 AI 誤判的列 attributions 是空陣列，若隱藏此鍵，那些列就再也進不去、還原不了——
                 而這顆鍵對零歸因的列反而最需要（還原誤判、補上 AI 漏判的歸因都靠它）。 -->
            <a-button
              class="!px-0"
              type="text"
              size="mini"
              :disabled="!canCorrect"
              @click="openCorrection(record)"
            >
              <template #icon><icon-edit /></template>
              人工糾正
            </a-button>
            <a-button class="!px-0" size="mini" type="text" @click="openTimeline(record, 'human')">
              <template #icon><icon-history /></template>
              人工歷史
            </a-button>
          </div>
        </div>
      </template>
    </TableLayout>

    <!-- 初判分類確認抽屜：選取範圍（已選內/全部）× 階段 × 目標篩選（自動帶入列表當前篩選，可重選）+ model -->
    <!-- 確認初判分類：批量（工具列）與單列（操作欄）共用同一抽屜，confirmScope 分流目標內容；
         模型 + 7 條 prompt 版本選擇兩者皆有；所有 Prompt 測試都在此進行，不支援測試未存檔草稿。 -->
    <a-drawer
      v-model:visible="confirmOpen"
      title="確認初判分類"
      :width="1040"
      :footer="false"
      :body-style="{ display: 'flex', flexDirection: 'column', overflow: 'hidden' }"
    >
      <div class="flex min-h-0 flex-1 flex-col gap-3 overflow-hidden">
        <!-- 左側收合軌 + 懸浮初判設定面板 + 主內容：面板用絕對定位懸浮在觸發 tab 右側（不佔版面
             寬度、不推擠內容），收合狀態下摘要卡／執行日誌直接貼齊左側 tab 顯示；面板本身用
             v-show（非 v-if）保持掛載，PromptVersionPickerGroup 的預設值/emit 即使收合也立即生效；
             面板內容一頁化（目標範圍＋初判設定順排全展開，無內層頁籤，開面板即見全部配置）。 -->
        <div class="relative flex min-h-0 flex-1 gap-3 overflow-hidden">
          <CollapsibleSidePanel v-model="confirmSettingsOpen" label="初判設定" floating fill>
            <template v-if="confirmScope === 'batch'">
              <a-divider orientation="left" :margin="12">目標範圍</a-divider>
              <!-- 選取範圍：有勾選列才提供「已選內」；階段+篩選對兩種範圍皆生效（已選內＝在勾選列集合中再交集）-->
              <div v-if="runCount" class="mb-3 flex items-center gap-2">
                <span class="text-xs text-gray-500">選取範圍</span>
                <a-radio-group v-model="targetMode" size="small" @change="refreshTargetCount">
                  <a-radio value="selected">已選 {{ runCount }} 筆內</a-radio>
                  <a-radio value="scope">全部資料</a-radio>
                </a-radio-group>
              </div>

              <div class="flex flex-col gap-3">
                <div>
                  <div class="mb-1 text-xs text-gray-500">
                    目標初判階段（預設只判未初判；加選已初判階段＝再判）
                  </div>
                  <a-checkbox-group v-model="targetStages" @change="refreshTargetCount">
                    <a-checkbox v-for="(lbl, code) in STAGE_LABELS" :key="code" :value="code">
                      {{ lbl }}
                    </a-checkbox>
                  </a-checkbox-group>
                </div>
                <!-- 目標篩選：共用 AttributionFilterBar（完整篩選欄，與列表對齊；自動帶入列表當前篩選，可重選）。
                     星等/日期/ID 兩分支皆套；傾向/信心分層/L1 為初判級，僅對已初判分支生效（見 usePrejudgeJob._scopeBody）。 -->
                <div>
                  <div class="mb-1 text-xs text-gray-500">
                    目標篩選（已自動帶入列表當前篩選，可重選）
                  </div>
                  <AttributionFilterBar
                    :model="draftFilters"
                    :fields="PREJUDGE_TARGET_FIELDS"
                    :cascade-options="cascadeOptions"
                    :id-placeholder="idPlaceholder"
                    @change="refreshTargetCount"
                  />
                  <div class="mt-1 text-xs text-gray-400">
                    日期 / ID / 外部評論 對所有目標生效；傾向 / 信心分層 / L1
                    僅對「已初判」階段生效（未初判列尚無初判可比對）。
                  </div>
                </div>
                <!-- 再判信心範圍：勾選任一已初判階段才顯示（原「再判收斂」的傾向/信心/L1 已併入上方統一篩選欄）-->
                <div v-if="hasJudgedStage" class="flex items-center gap-2">
                  <span class="text-xs text-gray-500">再判信心範圍</span>
                  <a-radio-group v-model="lowConfOnly" size="small" @change="refreshTargetCount">
                    <a-radio :value="true">僅低信心</a-radio>
                    <a-radio :value="false">全部信心</a-radio>
                  </a-radio-group>
                </div>
              </div>
            </template>

            <a-divider orientation="left" :margin="12">初判設定</a-divider>
            <div class="flex flex-col gap-3">
              <a-alert v-if="!Object.keys(llmProviderHasToken).length" type="warning">
                尚無可用 LLM 連線，請先至「設定 › LLM 設定」建立並保存 API Token。
              </a-alert>
              <div>
                <div class="mb-1 text-xs text-gray-500">模型配置</div>
                <LlmConfigSelect
                  v-model="llmConfigId"
                  :configs="llmConfigs"
                  :provider-has-token="llmProviderHasToken"
                />
              </div>
              <div>
                <div class="mb-1 text-xs text-gray-500">
                  Prompt 版本（每支預設沿用目前 active 版，可個別切換歷史版本）
                </div>
                <PromptVersionPickerGroup @update:resolved="(v) => (confirmVersionSelection = v)" />
              </div>
            </div>

            <!-- 動作列收在面板內（面板＝確認表單）：取消＝收合面板（不關抽屜）；確認＝依 scope
                 分流執行並自動收合面板改看執行日誌。 -->
            <template #footer>
              <a-button size="small" @click="confirmSettingsOpen = false">取消</a-button>
              <a-button
                type="primary"
                size="small"
                :loading="confirmScope === 'row' ? isRowBusy(confirmRowId) : running"
                @click="onConfirmRun"
              >
                確認
              </a-button>
            </template>
          </CollapsibleSidePanel>

          <!-- 主內容（恆顯示，不隨確認前後切換）：上＝本次執行摘要卡；下＝LLM 執行日誌
               （未執行時為日誌空狀態，確認後就地串流——不再另開獨立的 PrejudgeLogDrawer）。 -->
          <div class="flex min-w-0 flex-1 flex-col gap-3 overflow-hidden">
            <div class="flex flex-none flex-col gap-2 rounded-lg border p-4">
              <div class="text-sm text-[var(--color-text-1)]">
                <template v-if="confirmScope === 'batch'">
                  將對 <b class="text-[rgb(var(--primary-6))]">{{ targetCount }}</b>
                  筆進行初判分類（正向不分類；負向與含問題點的中性反饋歸 L1→L2）。
                </template>
                <template v-else>{{ rejudgeConfirmText }}</template>
              </div>
              <div class="flex items-baseline gap-2 text-sm">
                <span class="w-20 shrink-0 text-xs text-gray-500">模型</span>
                <span>{{ confirmModelLabel }}</span>
              </div>
              <div class="flex items-baseline gap-2 text-sm">
                <span class="w-20 shrink-0 text-xs text-gray-500">Prompt 版本</span>
                <span v-if="!confirmPinnedVersions.length">全部沿用目前 active 版</span>
                <div v-else class="flex flex-col gap-1">
                  <span>指定 {{ confirmPinnedVersions.length }} 支歷史版本：</span>
                  <span
                    v-for="[label, ver] in confirmPinnedVersions"
                    :key="label"
                    class="text-xs text-[var(--color-text-2)]"
                  >
                    {{ label }} → v{{ ver }}
                  </span>
                </div>
              </div>
              <div class="text-xs text-gray-400">
                在左側「初判設定」面板調整模型 / Prompt 版本並按「確認」後開始分類，過程會消耗
                token，執行日誌即時顯示於下方。
              </div>
            </div>
            <!-- 進度列（執行中）：單列/批量共用 jobStatus/progress（見 usePrejudgeJob）；
                 暫停/恢復/停止僅批量顯示（running＝batch 專屬旗標，單列 job 無控制意義）。 -->
            <div v-if="jobStatus" class="flex flex-none flex-col gap-1 rounded-lg border px-3 py-2">
              <div class="flex items-center gap-3">
                <a-progress
                  class="flex-1"
                  size="small"
                  :percent="progressPct / 100"
                  :status="
                    jobStatus === 'paused' ? 'warning' : progressPct >= 100 ? 'success' : 'normal'
                  "
                >
                  <template #text="{ percent }">{{ fmtPercent(percent) }}</template>
                </a-progress>
                <template v-if="running">
                  <a-button
                    v-if="jobStatus === 'paused'"
                    size="mini"
                    type="primary"
                    @click="resumeJob"
                  ><template #icon><icon-undo /></template>
                    恢復
                  </a-button>
                  <a-button
                    v-else
                    size="mini"
                    :disabled="jobStatus === 'cancelling'"
                    @click="pauseJob"
                  ><template #icon><icon-pause-circle /></template>
                    暫停
                  </a-button>
                  <a-popconfirm
                    content="確定停止？僅取消『尚未派發』的初判；已在進行的會判完（無法中途中斷）。故小批量可能已全部派發、停止近乎無效。已初判結果保留，剩餘可稍後重跑。"
                    @ok="cancelJob"
                  >
                    <a-button size="mini" status="danger" :disabled="jobStatus === 'cancelling'">
                      {{ jobStatus === 'cancelling' ? '停止中…' : '停止' }}
                    </a-button>
                  </a-popconfirm>
                </template>
              </div>
              <span class="text-xs text-[var(--color-text-3)]">
                {{
                  jobStatus === 'paused'
                    ? '已暫停'
                    : jobStatus === 'cancelling'
                      ? '停止中'
                      : '已處理'
                }}
                {{ progress.processed }}/{{ progress.total }} 筆
                <template v-if="costText"> · {{ costText }}</template>
              </span>
            </div>
            <!-- 終態摘要卡：上一輪已結束且未開新一輪（jobStatus 已清、lastRun 留存）——
                 讓「跑完發生了什麼」留在畫面上，不只靠一閃而過的 toast。 -->
            <div v-else-if="lastRun" class="flex flex-none flex-col gap-2 rounded-lg border p-3">
              <div class="flex items-center gap-2">
                <a-tag
                  size="small"
                  :color="
                    lastRun.status === 'done'
                      ? 'green'
                      : lastRun.status === 'error'
                        ? 'red'
                        : 'gray'
                  "
                >
                  {{
                    lastRun.status === 'done'
                      ? '完成'
                      : lastRun.status === 'error'
                        ? '失敗'
                        : '已停止'
                  }}
                </a-tag>
                <span class="text-sm">已處理 {{ lastRun.processed }}/{{ lastRun.total }} 筆</span>
              </div>
              <div class="text-xs text-[var(--color-text-3)]">
                模型 {{ lastRun.model }}
                <template v-if="lastRun.totalTokens">
                  · {{ lastRun.totalTokens.toLocaleString() }} tokens · ≈ ${{
                    lastRun.costUsd.toFixed(4)
                  }}
                </template>
              </div>
              <div class="flex gap-2">
                <a-button size="mini" type="text" @click="openLastRunLog"><template #icon><icon-code /></template>查看 LLM 日誌</a-button>
                <a-button size="mini" type="text" @click="runsDrawerVisible = true"><template #icon><icon-history /></template>
                  查看初判紀錄
                </a-button>
              </div>
            </div>
            <div class="flex min-h-0 flex-1 flex-col overflow-hidden rounded-lg border">
              <a-alert v-if="logError" type="info" class="flex-none">{{ logError }}</a-alert>
              <div class="min-h-0 flex-1 overflow-hidden">
                <PrejudgeLogView :entries="logEntries" :streaming="logStreaming" />
              </div>
            </div>
          </div>
        </div>
      </div>
    </a-drawer>

    <!-- 導出設定抽屜：草稿帶入列表當前篩選、可重選（共用 AttributionFilterBar）；有勾選則只導勾選列 -->
    <a-drawer
      v-model:visible="exportOpen"
      title="導出列表"
      ok-text="開始導出"
      cancel-text="取消"
      :width="1040"
      @ok="doExport"
    >
      <div class="flex flex-col gap-3">
        <div v-if="runCount" class="text-xs text-[rgb(var(--warning-6))]">
          已勾選 {{ runCount }} 筆 → 只導出勾選列（下方篩選僅供參考，不套用）。
        </div>
        <!-- 輸出結果版本：與「初判模型」篩選（圈哪些評論）語義獨立——這裡決定輸出「哪個模型判的內容」 -->
        <div>
          <div class="mb-1 text-xs text-gray-500">輸出結果版本（要看哪個模型判的結果）</div>
          <a-row :gutter="[8, 8]" align="center">
            <a-col flex="260px">
              <a-select
                v-model="exportSnapshotModel"
                size="small"
                allow-clear
                class="w-full"
                placeholder="當前判決結果（預設）"
                :options="modelOptions"
              />
            </a-col>
          </a-row>
          <!-- 兩種模式 + 篩選口徑分點說明（原單行三概念擠一起難讀）-->
          <div class="mt-1 space-y-0.5 text-xs text-gray-400">
            <div>
              <b class="font-medium text-gray-500">當前判決結果</b>
              ：每則評論輸出「最近一次初判」的內容——不同評論可能由不同模型判出（初判模型欄可辨識）。
            </div>
            <div>
              <b class="font-medium text-gray-500">選特定模型</b>
              ：改輸出「該模型判過的版本」（取其最新一次），用於多模型結果對比；該模型沒判過的評論不會出現在檔案中，明細與統計表都會換成該模型的結果。
            </div>
            <div>
              <b class="font-medium text-gray-500">注意</b>
              ：下方「導出範圍篩選」一律以<b>當前初判</b>決定哪些評論入選（例：篩「負向」＝當前初判為負向），與此處選的輸出版本無關。
            </div>
          </div>
        </div>
        <!-- 並排對比模型：基準（上方輸出版本，預設 gpt 當前初判）右側附各模型一組情緒/L1/L2 對比欄 -->
        <div>
          <div class="mb-1 text-xs text-gray-500">並排對比模型（可複選，附在基準右側逐列對照）</div>
          <a-row :gutter="[8, 8]" align="center">
            <a-col flex="420px">
              <a-select
                v-model="exportCompareModels"
                size="small"
                multiple
                allow-clear
                class="w-full"
                placeholder="不並排（僅基準）；可複選其他模型一起導出對比"
                :options="modelOptions"
                :max-tag-count="3"
              />
            </a-col>
          </a-row>
          <div class="mt-1 text-xs text-gray-400">
            每個選定模型在基準右側增加「情緒·M / L1·M / L2·M」三欄，值取該模型
            <b class="font-medium text-gray-500">最新一次初判</b>（attribution_history
            快照）；該模型未初判／判為無問題的評論該三欄留空。
          </div>
        </div>
        <div>
          <div class="mb-1 text-xs text-gray-500">導出範圍篩選（已帶入列表當前篩選，可重選）</div>
          <AttributionFilterBar
            :model="exportFilters"
            :fields="toolbarFields"
            :cascade-options="cascadeOptions"
            :model-options="modelOptions"
            :id-placeholder="idPlaceholder"
          />
        </div>
        <div class="text-xs text-gray-400">確認後於背景組檔，完成自動下載（可於進度條停止）。</div>
      </div>
    </a-drawer>

    <!-- 操作欄：查看歸因詳情抽屜（完整展示原文/關聯資料/每條歸因全欄位；抽出為獨立元件）-->
    <AttributionDetailDrawer v-model:visible="detailOpen" :row="detailRow" :source="source" />
  </div>
</template>

<style scoped>
/* ── 操作欄的分組（反饋 / 初判 / 判決 / 人工）─────────────────────────────────
   icon 依**分組**配、不是每顆各給一個：同一分組的動作共用同一個 icon，讓「這兩顆是同一類」
   不必讀文字就看得出來。四個 icon 各自的語義：
     IconFile    反饋詳情——這則反饋的完整資料
     IconRobot   初判分類 / 判決歸因——AI 產出的判定（工具列的批量初判分類**也是這個**，
                 批量與單列是同一個動作的兩種範圍，用不同 icon 會讓人以為是兩件事）
     IconHistory 初判歷史 / 判決歷史 / 人工歷史——回看時間軸
     IconEdit    人工糾正——人改值

   一組一行，**分類由按鈕文案自己承擔**——每顆都是四字完整文案，前兩字即所屬分類
   （反饋詳情｜初判分類·初判歷史｜判決歸因·判決歷史｜人工糾正·人工紀錄）。

   曾經試過「左側灰色分類標籤 + 兩字動詞」（初判 分類·歷史），兩個問題否決了它：
   ① 「分類」「歸因」單看有歧義、「歷史」還重複兩次，而螢幕閱讀器與鍵盤焦點只讀得到按鈕本身，
      讀不到旁邊那個標籤；② 全站其他動作標籤都是完整文案，兩字動詞會是唯一的例外。
   拿掉標籤欄之後，省下的 30px 剛好給完整文案用。（欄寬後續因分組按鈕與 icon 兩度加寬，
   後因改為自適應換行而收回 112；算式與實測依據見 source-schema.constant.ts 的操作欄註解。）

   四行的前兩字天然對齊成一欄，分組不必畫線也看得出來；每顆按鈕都有分類，沒有孤兒
   （落單的按鈕在 flex-col 容器裡會被 align-items:stretch 拉成整欄寬、文字置中，看起來像壞掉）。 */
.act-group {
  display: flex;
  /* 允許換行：窄欄時兩顆按鈕自動上下堆疊，寬欄時並排。
     這一行同時消滅了「操作欄被靜默裁切」這個失敗模式——**不夠寬就換行，不會切掉**，
     所以欄寬不再需要「寧可留白也不要裁切」的保守下限（那正是它一路被推到 180 的原因）。 */
  flex-wrap: wrap;
  align-items: baseline;
  /* 組內 4px < 組間 gap-2（8px）：分組靠間距層級表達，不靠分隔符號。 */
  gap: 4px;
  line-height: 1.35;
}
/* Arco text 按鈕預設 min-width/padding 會讓「初判分類 · 初判歷史」被撐開；歸零後靠 gap 控間距。 */
.act-group :deep(.arco-btn-text) {
  min-width: 0;
  height: auto;
  padding: 0;
  font-size: 12px;
  line-height: 1.35;
}
/* 複合評論欄星等縮小：Arco a-rate 預設星 ~20px 過大，主列精巧化縮至 14px，與傾向 tag / 標題同行不搶高。
   :deep 觸及 Arco 內部 .arco-rate-character（utility / prop 無法觸及第三方深層 DOM）。 */
:deep(.review-rate .arco-rate-character) {
  font-size: 14px;
  margin-right: 2px;
}
/**
 * 判決歸因合併欄：每條歸因一塊，塊間細線分隔（單欄內堆疊，無需跨欄等高，故不設 min-height）。
 *
 * 這裡的 6px 是**塊與塊之間的分隔留白**（配 border-top），與儲存格內距是不同語義——
 * 不能因為儲存格已統一 12px 就砍掉，砍了多條歸因會黏在一起。
 *
 * ⚠️ 用相鄰兄弟 `+` 而不是 `:first-child` / `:last-child` 歸零：本欄在 v-for 之前還有一個
 * v-if 的徽記列（AI 建議數／備註數），同層同為 div——**有徽記時第一塊就不是 `:first-child`**，
 * 舊寫法會多吐 6px 上內距（2026-08-07 實測重現：頂部變成 12+6=18px，與其餘四欄的 12px 對不齊，
 * 而且只在「這則反饋剛好有徽記」時才發生，是資料驅動的間歇性不一致）。改成 `+` 之後
 * 「第一塊沒有上內距、最後一塊沒有下內距」由結構保證，與徽記列在不在無關。
 * 視覺與舊版相同：塊間仍是 6px ─ 線 ─ 6px。
 */
.verdict-blk + .verdict-blk {
  margin-top: 6px;
  padding-top: 6px;
  border-top: 1px solid var(--color-neutral-3);
}
</style>

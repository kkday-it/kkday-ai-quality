<script setup lang="ts">
/**
 * 歸因列表（伺服器端分頁 + 選擇驅動初判歸因 + 正負傾向 + 原始+歸因合表）。
 *
 * 分頁/篩選/排序皆走後端（/api/problems limit-offset；occurred_at DESC 穩定）；表頭固定、表身內滾動、
 * 底部完整 Arco 分頁。選取跨頁累積（複選 / 分頁選取 / 全部未判 scope）；導出走後端全量 CSV。
 * 正向/中性 不歸因，只有負向才有 L1→L3。
 *
 * 資料/篩選/選取/初判歸因/導出邏輯下沉 `useAttributionList`；欄位/篩選器/展開行明細依來源切換
 * 讀 `SOURCE_LIST_SCHEMAS`（product_reviews 已打樣，其餘來源沿用固定欄位 fallback）。
 */
import { computed, defineAsyncComponent, nextTick, onMounted, ref } from 'vue';
import { Message } from '@arco-design/web-vue';
import {
  IconCheck,
  IconClose,
  IconDownload,
  IconEdit,
  IconHistory,
  IconMessage,
} from '@arco-design/web-vue/es/icon';
import {
  addFindingNote,
  evaluateTrueLabel,
  getFindingNotes,
  getTaxonomyCascade,
  updateTrueLabel,
  type CascadeNode,
  type FindingNote,
  type TrueLabelEval,
} from '@/api';
import { PERM } from '@/api';
import { ExportProgressBar, StateGuard, TableLayout } from '@/components';
import { usePermission } from '@/composables/usePermission';
import { composeLlmLabel } from '@/features/settings/utils';
import { AttributionDetailDrawer, AttributionFilterBar } from '../components';
import {
  POLARITY_LABELS,
  SOURCES,
  STAGE_LABELS,
  STATUS_COLOR,
  STATUS_LABEL,
  TIER_LABELS,
  TRAVELLER_TYPE_LABELS,
  type Attribution,
  type FilterField,
  type ProblemRow,
} from '../constants';
import { useAttributionList } from '../composables';
import { fmtDt } from '../utils';

/** 傾向類別標籤色（正向綠 / 負向紅 / 中性灰）。 */
const POLARITY_COLOR: Record<string, string> = {
  positive: 'green',
  negative: 'red',
  neutral: 'gray',
};

/** 判決階段語義色（未判灰 / 已判決綠 / 待覆核橙 / 待數據補充藍）。 */
const STAGE_COLOR: Record<string, string> = {
  unjudged: 'gray',
  judged: 'green',
  pending_review: 'orange',
  pending_data: 'arcoblue',
};

const SOURCE_OPTS = SOURCES.map((s) => ({ value: s.value, label: s.label }));

// 按鈕級權限遮罩（後端 403 兜底；此處 disabled 讓無權者一眼可辨「功能存在但不可用」）
const { can } = usePermission();
const canPrejudge = computed(() => can(PERM.judgmentPrejudgeRun));
const canReview = computed(() => can(PERM.findingReviewUpdate));
const canTrueLabel = computed(() => can(PERM.findingTrueLabelUpdate));
const canExport = computed(() => can(PERM.problemListExport));

// 歸因歷史抽屜（點開才載；每次批量/選取/單筆重判的 LLM 使用紀錄）
const JudgmentRunsDrawer = defineAsyncComponent(
  () => import('../components/JudgmentRunsDrawer.vue'),
);
const runsDrawerVisible = ref(false);

// 判決歷史彈窗（評論級時間軸：判決快照/覆核轉移/備註；點開才載）
const JudgmentHistoryModal = defineAsyncComponent(
  () => import('../components/JudgmentHistoryModal.vue'),
);
const historyOpen = ref(false);
const historyRow = ref<ProblemRow | null>(null);
/** 開某則評論的判決歷史時間軸（source_id 級；與 run 級「歸因歷史」抽屜不同層）。 */
const openJudgmentHistory = (record: ProblemRow) => {
  historyRow.value = record;
  historyOpen.value = true;
};

const source = ref('product_reviews');

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
  activeLlmId,
  setActiveLlm,
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
  openExport,
  doExport,
  exporting,
  exportStatus,
  exportProgress,
  exportPct,
  cancelExport,
  isRowBusy,
  rejudgeRow,
  reviewFinding,
  batchReview,
  init,
} = useAttributionList(source);

// 單列重判完成 + 重載後，把表身捲回剛判的那一列（大列表·表身內滾動 y='100%'，重載會回頂 → 失去位置）。
// ref 掛在 TableLayout（內建表格模式），內部 a-table 實例經其 expose 的 tableRef 取得。
const tableRef = ref<{ tableRef?: { $el: HTMLElement } | null } | null>(null);
const onRejudge = async (id: string) => {
  await rejudgeRow(id); // composable 內含 SSE 等待 + 重載本頁（同頁碼/排序 → 該列索引不變）
  await nextTick();
  const idx = rows.value.findIndex((r) => String(r._group) === id);
  if (idx < 0) return;
  const tr = tableRef.value?.tableRef?.$el?.querySelectorAll('.arco-table-body tbody > tr')[idx];
  (tr as HTMLElement | undefined)?.scrollIntoView({ block: 'center', behavior: 'auto' }); // 即時定位，無滾動動畫
};

// ── 操作：查看判決詳情彈窗（純前端，資料取自該列 attributions）──
const detailRow = ref<ProblemRow | null>(null);
const detailOpen = ref(false);
/** 開查看詳情彈窗。 */
const viewDetail = (record: ProblemRow) => {
  detailRow.value = record;
  detailOpen.value = true;
};
/** 歸因詳情：把一條歸因的 L1→L3 併成麵包屑字串。 */
const attrPath = (a: Attribution): string =>
  [a.l1?.label, a.l2?.label, a.l3?.label].filter(Boolean).join(' › ') || '未歸因';

/**
 * 信心數字按分層上色（config confidence_tiers 驅動的 tier）：
 * auto_accept(≥0.8) 綠＝可採信 / jury(0.5–0.8) 琥珀＝需覆核 / needs_review(<0.5) 紅＝必人工。
 * 讓覆核者掃一眼信心色就知哪條要處理（呼應「< 0.8 需人工覆核」）。
 */
const CONF_TIER_CLASS: Record<string, string> = {
  auto_accept: 'text-[rgb(var(--success-6))]',
  jury: 'text-[rgb(var(--warning-6))]',
  needs_review: 'text-[rgb(var(--danger-6))]',
};
const confClass = (tier?: string): string =>
  CONF_TIER_CLASS[tier || ''] || 'text-[var(--color-text-1)]';

// ── 外部評論融合欄（sentiment / free_tag 輔助訊號）顯示輔助 ──
/** 外部情緒分上色：1-2 負向紅、3 中性琥珀、4-5 正向綠（對齊評論系統分段定義）。 */
const extSentimentClass = (v?: string | number | null): string => {
  const n = Number(v);
  if (!Number.isFinite(n)) return 'text-[var(--color-text-1)]';
  if (n <= 2) return 'text-[rgb(var(--danger-6))]';
  if (n < 4) return 'text-[rgb(var(--warning-6))]';
  return 'text-[rgb(var(--success-6))]';
};
/** free_tag 面向分 → Arco tag color（低分痛點紅、中性橙、高分綠）。 */
const extTagColor = (v?: string | number | null): string => {
  const n = Number(v);
  if (!Number.isFinite(n)) return 'gray';
  if (n <= 2) return 'red';
  if (n < 4) return 'orange';
  return 'green';
};

// ── 操作欄：標真值彈窗（級聯選 L1→L2→L3 → LLM 評分把關 → 低信心填理由 → 標註）──
const truelabelOpen = ref(false);
const truelabelRow = ref<ProblemRow | null>(null);
const truelabelSaving = ref(false);
/** 兩階段：select＝選級聯；review＝看 LLM 前後信心對比 + 低信心填理由。 */
const truelabelPhase = ref<'select' | 'review'>('select');
/** 級聯樹選項（懶載一次；全 finding 共用）。 */
const cascadeOpts = ref<CascadeNode[]>([]);
const cascadeLoading = ref(false);
/** finding_id → 選定的真值 code（級聯葉；空＝清除標註）。 */
const truelabelDraft = ref<Record<string, string>>({});
/** finding_id → LLM 評分結果（review 階段顯示前後信心對比）。 */
const truelabelEvals = ref<Record<string, TrueLabelEval>>({});
/** finding_id → 修改理由（低信心 reason_required 時必填）。 */
const truelabelReasons = ref<Record<string, string>>({});

/** 懶載級聯樹（首次開彈窗才拉，之後快取）。 */
const ensureCascade = async () => {
  if (cascadeOpts.value.length || cascadeLoading.value) return;
  cascadeLoading.value = true;
  try {
    cascadeOpts.value = await getTaxonomyCascade();
  } catch (e: any) {
    Message.error('載入分類樹失敗：' + (e?.message || e));
  } finally {
    cascadeLoading.value = false;
  }
};

/** 開標真值彈窗：以各歸因現有 true_label 為初值，回到 select 階段。 */
const openTrueLabel = (record: ProblemRow) => {
  if (!record.attributions || !record.attributions.length) {
    Message.warning('此列尚無歸因可標註，請先歸因');
    return;
  }
  truelabelRow.value = record;
  truelabelPhase.value = 'select';
  truelabelDraft.value = {};
  truelabelEvals.value = {};
  truelabelReasons.value = {};
  record.attributions.forEach((a) => {
    if (a.finding_id) truelabelDraft.value[a.finding_id] = a.true_label || '';
  });
  truelabelOpen.value = true;
  void ensureCascade();
};

/** 本次有選定真值（非清除）的歸因清單。 */
const labeledAttrs = computed(() =>
  (truelabelRow.value?.attributions || []).filter(
    (a) => a.finding_id && (truelabelDraft.value[a.finding_id] || '').trim(),
  ),
);
/** review 階段：仍有 reason_required 但未填理由者 → 阻擋確認。 */
const reasonBlocked = computed(() =>
  labeledAttrs.value.some((a) => {
    const fid = a.finding_id as string;
    return (
      truelabelEvals.value[fid]?.reason_required && !(truelabelReasons.value[fid] || '').trim()
    );
  }),
);

/** 評估並標註（select→review）：對有選定真值的歸因逐一 LLM 評分，收齊後進 review 顯示前後信心對比。 */
const evaluateTrueLabels = async () => {
  const attrs = labeledAttrs.value;
  if (!attrs.length) {
    // 全為清除 → 無需評分，直接標註（清除）
    await confirmTrueLabels();
    return;
  }
  truelabelSaving.value = true;
  try {
    const results = await Promise.all(
      attrs.map((a) =>
        evaluateTrueLabel(a.finding_id as string, truelabelDraft.value[a.finding_id as string]),
      ),
    );
    const evals: Record<string, TrueLabelEval> = {};
    attrs.forEach((a, i) => (evals[a.finding_id as string] = results[i]));
    truelabelEvals.value = evals;
    truelabelPhase.value = 'review';
  } catch (e: any) {
    Message.error('評分失敗：' + (e?.message || e));
  } finally {
    truelabelSaving.value = false;
  }
};

/** review 階段信心對比顯示：回 {text, cls}（升綠降紅）。 */
const evalDelta = (ev: TrueLabelEval): { text: string; cls: string } => {
  const llm = ev.llm_confidence.toFixed(2);
  if (ev.delta == null || ev.original_confidence == null) {
    return { text: `LLM 對此真值信心 ${llm}`, cls: 'text-[var(--color-text-2)]' };
  }
  const up = ev.delta >= 0;
  return {
    text: `LLM 對此真值信心 ${llm}（原判 ${ev.original_confidence.toFixed(2)}，${up ? '↑' : '↓'}${Math.abs(ev.delta).toFixed(2)}）`,
    cls: up ? 'text-[rgb(var(--green-6))]' : 'text-[rgb(var(--red-6))]',
  };
};

/** 確認標註（review→存）：逐歸因 PATCH（帶理由 + LLM 信心）；清除者送 null。optimistic 回寫。 */
const confirmTrueLabels = async () => {
  const row = truelabelRow.value;
  if (!row) return;
  if (reasonBlocked.value) {
    Message.warning('部分真值信心明顯偏低，請填寫修改理由');
    return;
  }
  const attrs = (row.attributions || []).filter((a) => a.finding_id);
  truelabelSaving.value = true;
  try {
    await Promise.all(
      attrs.map((a) => {
        const fid = a.finding_id as string;
        const val = (truelabelDraft.value[fid] || '').trim() || null;
        const ev = truelabelEvals.value[fid];
        return updateTrueLabel(fid, val, {
          reason: truelabelReasons.value[fid] || undefined,
          llmConf: ev?.llm_confidence,
        });
      }),
    );
    attrs.forEach(
      (a) =>
        (a.true_label = (truelabelDraft.value[a.finding_id as string] || '').trim() || undefined),
    );
    Message.success('已標註真值');
    truelabelOpen.value = false;
  } catch (e: any) {
    Message.error('標註失敗：' + (e?.message || e));
  } finally {
    truelabelSaving.value = false;
  }
};

// ── 歸因備註（append-only 歷史：備註人 / 時間 / 內容）──
const noteOpen = ref(false);
const noteFindingId = ref('');
const noteList = ref<FindingNote[]>([]);
const noteDraft = ref('');
const noteLoading = ref(false);
const noteSaving = ref(false);

/** 開某條歸因的備註彈窗並載入歷史。 */
const openNotes = async (findingId: string) => {
  noteFindingId.value = findingId;
  noteDraft.value = '';
  noteList.value = [];
  noteOpen.value = true;
  noteLoading.value = true;
  try {
    noteList.value = await getFindingNotes(findingId);
  } catch (e: any) {
    Message.error('載入備註失敗：' + (e?.message || e));
  } finally {
    noteLoading.value = false;
  }
};

/** 送出一則備註（備註人由後端登入身分帶入），成功後置頂插入歷史。 */
const submitNote = async () => {
  const content = noteDraft.value.trim();
  if (!content) return;
  noteSaving.value = true;
  try {
    const created = await addFindingNote(noteFindingId.value, content);
    noteList.value = [created, ...noteList.value];
    noteDraft.value = '';
    Message.success('已新增備註');
  } catch (e: any) {
    Message.error('新增備註失敗：' + (e?.message || e));
  } finally {
    noteSaving.value = false;
  }
};

/** 備註時間顯示（ISO → 'YYYY-MM-DD HH:mm:ss'）。 */
const fmtNoteTime = (iso: string | null): string => (iso ? iso.replace('T', ' ').slice(0, 19) : '');

const LLM_OPTS = computed(() =>
  llmConfigs.value.map((c) => ({ value: c.id, label: composeLlmLabel(c) })),
);

/** 本次判決將使用的模型 label（llmConfigId 跟隨全域啟用中，modal 可臨時覆寫）；無配置回空。 */
const currentLlmLabel = computed(() => {
  const c = llmConfigs.value.find((x) => x.id === llmConfigId.value);
  return c ? composeLlmLabel(c) : '';
});

// 工具列全域模型切換：持久化 active_llm_config_id（與設定抽屜同一寫入路徑，雙向即時同步）
const switchingLlm = ref(false);
const onSwitchLlm = async (id: unknown) => {
  switchingLlm.value = true;
  try {
    await setActiveLlm(String(id));
    Message.success(`歸因模型已切換：${currentLlmLabel.value || String(id)}`);
  } catch (e: any) {
    Message.error('模型切換失敗：' + (e?.message || e));
  } finally {
    switchingLlm.value = false;
  }
};

/** 單列初判歸因（已有歸因時＝重新判決覆寫）二次確認文案（附當前模型，判前提醒用什麼 model 歸因）。 */
const rejudgeConfirmText = computed(
  () =>
    `將以「${currentLlmLabel.value || '（無 LLM 配置）'}」重新初判並覆寫此列現有歸因（人工真值標註保留），並消耗判決額度。確定執行？`,
);

/** schema filter type → AttributionFilters 欄位鍵（現皆同名，保留映射以隔離 schema 命名）。 */
const SCHEMA_TO_FIELD: Record<string, FilterField> = {
  polarity: 'polarity',
  stage: 'stage',
  tier: 'tier',
  status: 'status',
  model: 'model',
  taxonomy: 'taxonomy',
  hasExternal: 'hasExternal',
  dateRange: 'dateRange',
};
/** 工具列篩選欄位：schema 決定的維度 + 通用精確查詢（rec/prod/order id 恆顯示）。 */
const toolbarFields = computed<FilterField[]>(() => {
  const fromSchema = schema.value.filters
    .map((f) => SCHEMA_TO_FIELD[f.type])
    .filter((k): k is FilterField => Boolean(k));
  return [...fromSchema, 'recOid', 'prodOid', 'orderOid'];
});
/** 初判彈窗「目標篩選」欄位：統一完整篩選欄（與列表對齊）。第一行 id/日期，第二行 傾向/信心分層/歸因分類/外部評論。
 *  日期/id/外部評論 為表級（兩分支皆套）；傾向/信心分層/歸因分類 為判決級（只對已判分支生效，見 _scopeBody
 *  的 hasJudgedStage 閘）。判決階段由上方 checkbox 承擔 → 不納入此篩選欄。 */
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

/** 欄位缺值防禦顯示（'—'）；部分來源（mixpanel）OID 為 JSON 陣列字串 `["x"]` → 攤平顯示。 */
const cell = (v: unknown): string => {
  if (v === null || v === undefined || v === '') return '—';
  const s = String(v);
  if (s.startsWith('[') && s.endsWith(']')) {
    try {
      const arr = JSON.parse(s);
      if (Array.isArray(arr)) return arr.length ? arr.map(String).join('、') : '—';
    } catch {
      /* 非 JSON 陣列 → 原樣顯示 */
    }
  }
  return s;
};

onMounted(init);
</script>

<template>
  <!-- 初判歸因控制列送進固定工具列橫帶（tab 下方），與歸因概覽一致、恆常可見 -->
  <Teleport to="#page-toolbar">
    <div class="flex items-center gap-3">
      <span class="text-sm text-gray-500">反饋來源</span>
      <a-select
        v-model="source"
        size="small"
        style="width: 150px"
        :options="SOURCE_OPTS"
        @change="onFilterChange"
      />
      <!-- 商品垂直分類複選（全局 SSOT；預設全選，剩 1 不可移除；即使全選也嚴格限制在所選分類內）-->
      <span class="text-sm text-gray-500">商品垂直分類</span>
      <a-select
        :model-value="verticalGroups"
        multiple
        size="small"
        style="width: 220px"
        :max-tag-count="1"
        placeholder="選分類分組"
        :options="verticalOptions.map((g) => ({ value: g, label: g }))"
        @change="onVerticalChange"
      />
      <!-- 歸因模型全域切換：值＝啟用中配置（與設定抽屜雙向即時同步）；切換即持久化 active_llm_config_id -->
      <span class="text-sm text-gray-500">歸因模型</span>
      <a-select
        :model-value="activeLlmId"
        size="small"
        style="width: 230px"
        :options="LLM_OPTS"
        :loading="switchingLlm"
        placeholder="無 LLM 配置（去設定新增）"
        @change="onSwitchLlm"
      />
      <!-- 統一操作區：主行為 primary、次要 outline（見 rules/frontend-vue.md 按鈕規範）-->
      <a-button
        type="primary"
        size="small"
        :loading="running"
        :disabled="!canPrejudge"
        @click="openPrejudge"
      >
        初判歸因{{ runCount ? `（已選 ${runCount}）` : '' }}
      </a-button>
      <!-- 歸因歷史：純檢視（每次批量/選取/單筆重判的 LLM 使用紀錄），緊鄰初判入口 -->
      <a-button size="small" type="text" @click="runsDrawerVisible = true">
        <template #icon><icon-history /></template>
        歸因歷史
      </a-button>
      <a-button
        size="small"
        type="outline"
        :loading="exporting"
        :disabled="!canExport"
        @click="openExport"
      >
        <template #icon><icon-download /></template>
        導出列表{{ runCount ? `（已選 ${runCount}）` : '' }}
      </a-button>
    </div>
  </Teleport>

  <!-- 歸因歷史抽屜（懶載；unmount-on-close）-->
  <JudgmentRunsDrawer v-model:visible="runsDrawerVisible" />

  <!-- 判決歷史彈窗（評論級時間軸；懶載）-->
  <JudgmentHistoryModal v-model:visible="historyOpen" :source="source" :row="historyRow" />

  <div class="flex h-full flex-col gap-4">
    <!-- 初判歸因進度：批量判決進行中才顯示（控制列已移入工具列橫帶）-->
    <div v-if="running" class="rounded-md border border-[#f0f0f0] bg-white px-4 py-3">
      <div class="flex items-center gap-3">
        <a-progress
          class="flex-1"
          :percent="progressPct / 100"
          :status="jobStatus === 'paused' ? 'warning' : progressPct >= 100 ? 'success' : 'normal'"
        />
        <!-- 一鍵暫停/恢復/停止：依 jobStatus 切換 -->
        <a-button v-if="jobStatus === 'paused'" size="small" type="primary" @click="resumeJob">
          恢復
        </a-button>
        <a-button v-else size="small" :disabled="jobStatus === 'cancelling'" @click="pauseJob">
          暫停
        </a-button>
        <a-popconfirm
          content="確定停止？僅取消『尚未派發』的判決；已在進行的會判完（無法中途中斷）。故小批量可能已全部派發、停止近乎無效。已判結果保留，剩餘可稍後重跑。"
          @ok="cancelJob"
        >
          <a-button size="small" status="danger" :disabled="jobStatus === 'cancelling'">
            {{ jobStatus === 'cancelling' ? '停止中…' : '停止' }}
          </a-button>
        </a-popconfirm>
      </div>
      <div class="mt-1 flex flex-wrap gap-x-4 text-xs text-gray-500">
        <span>
          {{ jobStatus === 'paused' ? '已暫停' : jobStatus === 'cancelling' ? '停止中' : '已處理' }}
          {{ progress.processed }} / {{ progress.total }} 筆…
        </span>
        <span v-if="costText">花費 {{ costText }}</span>
      </div>
    </div>

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
      :title="`歸因列表（共 ${total} · 未判 ${unjudged}）`"
      hint="伺服器端分頁；勾選/分頁選取做初判歸因或導出"
      :data="rows"
      :columns="COLS"
      :loading="loading"
      :error="error"
      empty-text="尚無資料，請先到「資料上傳」上傳 CSV"
      server
      :total="total"
      :row-selection="{ type: 'checkbox', selectedRowKeys, showCheckedAll: true }"
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
            <a-button size="small" @click="selectPages">選取分頁</a-button>
          </a-col>
          <a-col flex="none">
            <!-- 常駐可見以利發現「取消選擇」；無選取時 disabled（非 v-if 隱藏） -->
            <a-button size="small" :disabled="!runCount" @click="clearSelection">清除選擇</a-button>
          </a-col>
          <!-- 批量覆核：作用於已勾選評論的**全部**歸因（同值列冪等跳過；轉移記入判決歷史）-->
          <a-col flex="none">
            <a-button
              size="small"
              type="outline"
              status="success"
              :disabled="!runCount || !canReview"
              @click="batchReview('confirmed')"
            >
              <template #icon><IconCheck /></template>
              批量確認
            </a-button>
          </a-col>
          <a-col flex="none">
            <a-popconfirm
              :content="`將把已選 ${runCount} 則評論的全部歸因標為「已忽略」。確定執行？`"
              ok-text="批量忽略"
              cancel-text="取消"
              @ok="batchReview('dismissed')"
            >
              <a-button
                size="small"
                type="outline"
                status="warning"
                :disabled="!runCount || !canReview"
              >
                <template #icon><IconClose /></template>
                批量忽略
              </a-button>
            </a-popconfirm>
          </a-col>
          <a-col flex="auto" class="flex items-center justify-end gap-2">
            <span v-if="activeFilterCount" class="text-xs text-[rgb(var(--primary-6))]">
              已套用 {{ activeFilterCount }} 項篩選
            </span>
            <span class="text-xs text-gray-400">每頁 {{ pageSize }} · 已選 {{ runCount }}</span>
            <a-button size="small" type="outline" status="warning" @click="resetFilters"
              >重置篩選</a-button
            >
          </a-col>
        </a-row>
      </template>
      <template #seq="{ record }">{{ record._seq }}</template>
      <!-- 反饋內容欄：比照關聯資料左標籤式，分「原始評論」（星等+傾向+標題/內容/#ID·時間）
           與「外部評論」（評論系統融合維度：sentiment 情緒分 + free_tag 面向標籤；輔助訊號，僅有值才顯示）。 -->
      <template #review="{ record }">
        <div class="flex flex-col gap-1 py-1">
          <!-- 原始評論 -->
          <div class="flex gap-1.5">
            <span
              class="flex min-w-[3rem] shrink-0 items-center justify-center self-stretch whitespace-nowrap rounded bg-[var(--color-fill-2)] px-1.5 py-0.5 text-center text-[11px] font-medium text-[var(--color-text-2)]"
              >原始評論</span
            >
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
                <span v-else class="text-xs text-gray-300">未判</span>
                <!-- 我方情緒分 1-5（重判後回填；與外部評論情緒分同尺度直接對比）-->
                <span v-if="record.our_sentiment" class="flex items-center gap-1 text-xs">
                  <span class="text-[var(--color-text-3)]">情緒分</span>
                  <span class="font-semibold" :class="extSentimentClass(record.our_sentiment)">
                    {{ record.our_sentiment }}/5
                  </span>
                </span>
                <span v-if="record.title" class="text-sm font-medium text-[var(--color-text-1)]">
                  {{ record.title }}
                </span>
              </div>
              <div
                v-if="record.content"
                class="whitespace-pre-wrap text-xs leading-relaxed text-[var(--color-text-2)]"
              >
                {{ record.content }}
              </div>
              <div class="mt-0.5 text-[11px] text-[var(--color-text-3)]">
                #{{ record.source_record_id || record.source_id || '—' }} ·
                {{ fmtDt(record.occurred_at) || '—' }}
              </div>
            </div>
          </div>
          <!-- 外部評論（評論系統 LLM 標籤；無融合資料的列不顯示此塊）-->
          <div
            v-if="record.ext_sentiment || record.ext_free_tag?.length"
            class="flex gap-1.5 border-t border-[var(--color-border-1)] pt-1"
          >
            <span
              class="flex min-w-[3rem] shrink-0 items-center justify-center self-stretch whitespace-nowrap rounded bg-[var(--color-fill-2)] px-1.5 py-0.5 text-center text-[11px] font-medium text-[var(--color-text-2)]"
              >外部評論</span
            >
            <div class="min-w-0 text-xs leading-relaxed">
              <div
                v-if="record.ext_sentiment"
                class="mb-0.5 flex flex-wrap items-center gap-x-2 gap-y-1"
              >
                <span class="text-[var(--color-text-3)]">情緒分</span>
                <span class="font-semibold" :class="extSentimentClass(record.ext_sentiment)">
                  {{ record.ext_sentiment }} / 5
                </span>
              </div>
              <!-- 每面向一行：tag_name（按分上色 tag）｜tag_value（獨立上色數字）｜tag_list（逐詞 Arco tag）-->
              <div
                v-for="(t, ti) in record.ext_free_tag || []"
                :key="ti"
                class="mb-0.5 flex flex-wrap items-center gap-x-1.5 gap-y-1"
              >
                <span
                  v-if="t.tag_value !== null && t.tag_value !== undefined && t.tag_value !== ''"
                  class="font-semibold"
                  :class="extSentimentClass(t.tag_value)"
                >
                  {{ t.tag_value }}
                </span>
                <a-tag size="small" :color="extTagColor(t.tag_value)">{{ t.tag_name }}</a-tag>
                <a-tag v-for="(w, wi) in t.tag_list || []" :key="wi" size="small" color="gray">
                  {{ w }}
                </a-tag>
              </div>
              <div v-if="record.ext_lst_oid" class="mt-0.5 text-[11px] text-[var(--color-text-3)]">
                ext#{{ record.ext_lst_oid }}
              </div>
            </div>
          </div>
        </div>
      </template>
      <!-- 判決歸因合併欄：每條歸因一塊（L1→L3 + 信心 + 分層 + 判決階段 全放一起），
               塊間細線分隔；多歸因並存時逐塊堆疊，資訊聚合、一眼看完整判決。 -->
      <template #verdict="{ record }">
        <template v-if="record.attributions && record.attributions.length">
          <!-- 每條歸因一塊，比照關聯資料欄：左小標籤（摘要/歸因/信心/操作）+ 右內容或操作 -->
          <div
            v-for="(a, ai) in record.attributions"
            :key="ai"
            class="verdict-blk flex flex-col gap-1 py-1 text-xs leading-relaxed"
          >
            <!-- 摘要（LLM 繁中概括，顯明；僅有值才顯示）-->
            <div v-if="a.content?.summary" class="flex gap-1.5">
              <span
                class="flex min-w-[3rem] shrink-0 items-center justify-center self-stretch whitespace-nowrap rounded bg-[var(--color-fill-2)] px-1.5 py-0.5 text-center text-[11px] font-medium text-[var(--color-text-2)]"
                >摘要</span
              >
              <div class="min-w-0 font-medium leading-snug text-[var(--color-text-1)]">
                {{ a.content.summary }}
              </div>
            </div>
            <!-- 歸因（L1→L3 麵包屑 + 真值徽章）-->
            <div class="flex gap-1.5">
              <span
                class="flex min-w-[3rem] shrink-0 items-center justify-center self-stretch whitespace-nowrap rounded bg-[var(--color-fill-2)] px-1.5 py-0.5 text-center text-[11px] font-medium text-[var(--color-text-2)]"
                >歸因</span
              >
              <div class="min-w-0">
                <template v-if="[a.l1?.label, a.l2?.label, a.l3?.label].some(Boolean)">
                  <template
                    v-for="(lvl, li) in [a.l1?.label, a.l2?.label, a.l3?.label].filter(Boolean)"
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
                <a-tooltip v-if="a.true_label" :content="`真值：${a.true_label}`">
                  <span class="ml-1.5 text-[11px] text-[rgb(var(--success-6))]">✔真值</span>
                </a-tooltip>
              </div>
            </div>
            <!-- 信心（值 + 分層 + 判決模型；stage 僅異常態顯示——三軸標籤收斂：status 移操作列）-->
            <div class="flex gap-1.5">
              <span
                class="flex min-w-[3rem] shrink-0 items-center justify-center self-stretch whitespace-nowrap rounded bg-[var(--color-fill-2)] px-1.5 py-0.5 text-center text-[11px] font-medium text-[var(--color-text-2)]"
                >信心</span
              >
              <div class="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
                <!-- 信心按 tier 上色：綠可採信 / 琥珀需覆核 / 紅必人工（< 0.8 需人工覆核）-->
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
                <!-- 判決模型（溯源；重判後更新）-->
                <a-tag v-if="a.model" size="small" color="purple">{{ a.model }}</a-tag>
                <!-- 判決階段：僅非 judged 的異常態才提示（已判決＝常態不佔位；全量三軸見詳情抽屜）-->
                <a-tag
                  v-if="a.stage && a.stage !== 'judged'"
                  size="small"
                  :color="STAGE_COLOR[a.stage]"
                >
                  {{ STAGE_LABELS[a.stage] || a.stage }}
                </a-tag>
              </div>
            </div>
            <!-- 操作（覆核徽章 + 確認採信(綠)/忽略駁回(紅)/標真值/備註；再點選中態＝撤銷覆核）-->
            <div class="flex gap-1.5">
              <span
                class="flex min-w-[3rem] shrink-0 items-center justify-center self-stretch whitespace-nowrap rounded bg-[var(--color-fill-2)] px-1.5 py-0.5 text-center text-[11px] font-medium text-[var(--color-text-2)]"
                >操作</span
              >
              <div class="flex min-w-0 flex-wrap items-center gap-1.5">
                <a-tag
                  v-if="a.status && a.status !== 'new'"
                  size="small"
                  :color="STATUS_COLOR[a.status]"
                  bordered
                >
                  {{ STATUS_LABEL[a.status] || a.status }}
                </a-tag>
                <a-tooltip :content="a.status === 'confirmed' ? '再點一次撤銷覆核' : '確認採信'">
                  <a-button
                    size="mini"
                    type="text"
                    status="success"
                    :class="
                      a.status === 'confirmed'
                        ? 'rounded bg-[var(--color-fill-2)] font-semibold'
                        : ''
                    "
                    :disabled="!canReview"
                    @click="reviewFinding(a, 'confirmed')"
                  >
                    <template #icon><IconCheck /></template>
                    確認
                  </a-button>
                </a-tooltip>
                <a-tooltip :content="a.status === 'dismissed' ? '再點一次撤銷覆核' : '忽略駁回'">
                  <a-button
                    size="mini"
                    type="text"
                    status="danger"
                    :class="
                      a.status === 'dismissed'
                        ? 'rounded bg-[var(--color-fill-2)] font-semibold'
                        : ''
                    "
                    :disabled="!canReview"
                    @click="reviewFinding(a, 'dismissed')"
                  >
                    <template #icon><IconClose /></template>
                    忽略
                  </a-button>
                </a-tooltip>
                <a-button
                  size="mini"
                  type="text"
                  :disabled="!canTrueLabel"
                  @click="openTrueLabel(record)"
                >
                  <template #icon><IconEdit /></template>
                  標真值
                </a-button>
                <a-badge v-if="a.finding_id" :count="a.notes_count || 0" :max-count="99">
                  <a-button size="mini" type="text" @click="openNotes(a.finding_id)">
                    <template #icon><IconMessage /></template>
                    備註
                  </a-button>
                </a-badge>
              </div>
            </div>
          </div>
        </template>
        <span v-else class="text-gray-300">—</span>
      </template>
      <!-- 關聯資料合併欄：訂單 → 商品 → 方案 → 供應商 → 旅客（源數據），各段左側小標籤（name）
               + 右側內容（值），主要值深色、次要明細 --color-text-2（加深，避免太淺看不清）。 -->
      <template #context="{ record }">
        <div class="flex flex-col gap-1 py-1 text-xs leading-relaxed">
          <!-- 訂單 -->
          <div class="flex gap-1.5">
            <span
              class="min-w-[3rem] shrink-0 rounded bg-[var(--color-fill-2)] px-1.5 py-0.5 text-center text-[11px] font-medium text-[var(--color-text-2)]"
              >訂單</span
            >
            <div class="min-w-0">
              <div class="font-medium text-[var(--color-text-1)]">{{ cell(record.order_mid) }}</div>
              <div class="text-[var(--color-text-2)]">
                OID {{ cell(record.order_oid) }} · 出發 {{ fmtDt(record.go_date, true) || '—' }}
              </div>
            </div>
          </div>
          <!-- 商品 -->
          <div class="flex gap-1.5">
            <span
              class="min-w-[3rem] shrink-0 rounded bg-[var(--color-fill-2)] px-1.5 py-0.5 text-center text-[11px] font-medium text-[var(--color-text-2)]"
              >商品</span
            >
            <div class="min-w-0">
              <div v-if="record.prod_name" class="font-medium text-[var(--color-text-1)]">
                {{ record.prod_name }}
              </div>
              <span v-else class="text-gray-300">—</span>
              <div class="text-[var(--color-text-2)]">
                OID {{ cell(record.prod_oid) }} · {{ cell(record.product_category_main) }} ·
                {{ cell(record.lang) }}
              </div>
            </div>
          </div>
          <!-- 方案 -->
          <div class="flex gap-1.5">
            <span
              class="min-w-[3rem] shrink-0 rounded bg-[var(--color-fill-2)] px-1.5 py-0.5 text-center text-[11px] font-medium text-[var(--color-text-2)]"
              >方案</span
            >
            <div class="min-w-0">
              <div v-if="record.package_name" class="text-[var(--color-text-1)]">
                {{ record.package_name }}
              </div>
              <span v-else class="text-gray-300">—</span>
              <div class="text-[var(--color-text-2)]">OID {{ cell(record.pkg_oid) }}</div>
            </div>
          </div>
          <!-- 供應商 -->
          <div class="flex gap-1.5">
            <span
              class="min-w-[3rem] shrink-0 rounded bg-[var(--color-fill-2)] px-1.5 py-0.5 text-center text-[11px] font-medium text-[var(--color-text-2)]"
              >供應商</span
            >
            <span class="text-[var(--color-text-1)]">{{ cell(record.supplier_oid) }}</span>
          </div>
          <!-- 旅客 -->
          <div class="flex items-center gap-1.5">
            <span
              class="min-w-[3rem] shrink-0 rounded bg-[var(--color-fill-2)] px-1.5 py-0.5 text-center text-[11px] font-medium text-[var(--color-text-2)]"
              >旅客</span
            >
            <a-tag v-if="record.traveller_type" size="small" color="arcoblue">
              {{ TRAVELLER_TYPE_LABELS[String(record.traveller_type)] || record.traveller_type }}
            </a-tag>
            <span v-if="record.member_uuid" class="break-all text-[var(--color-text-2)]">
              {{ record.member_uuid }}
            </span>
          </div>
        </div>
      </template>
      <!-- 操作欄：整列級動作全展開（初判歸因 + 查看詳情）；per-歸因 覆核在判決歸因欄內。與批量選取解耦。 -->
      <template #actions="{ record }">
        <div class="flex flex-col items-stretch gap-1.5 py-1">
          <!-- 已有歸因時＝覆寫破壞性（AI 覆寫既有歸因 + 燒判決額度）→ 二次確認；首次無覆寫，直接執行不製造確認疲勞 -->
          <a-popconfirm
            v-if="record.attributions && record.attributions.length"
            :content="rejudgeConfirmText"
            ok-text="初判歸因"
            cancel-text="取消"
            @ok="onRejudge(record._group)"
          >
            <a-button
              type="primary"
              size="small"
              :loading="isRowBusy(record._group)"
              :disabled="!canPrejudge"
            >
              初判歸因
            </a-button>
          </a-popconfirm>
          <a-button
            v-else
            type="primary"
            size="small"
            :loading="isRowBusy(record._group)"
            :disabled="!canPrejudge"
            @click="onRejudge(record._group)"
          >
            初判歸因
          </a-button>
          <!-- 未判亦可查看：抽屜的原文/關聯資料恆常可看，歸因區塊空時走 a-empty 佔位 -->
          <a-button size="small" type="outline" @click="viewDetail(record)"> 查看詳情 </a-button>
          <!-- 判決歷史（評論級時間軸：歷次判決快照/覆核轉移/備註；輕量檢視 → text）-->
          <a-button size="small" type="text" @click="openJudgmentHistory(record)">
            <template #icon><IconHistory /></template>
            判決歷史
          </a-button>
        </div>
      </template>
    </TableLayout>

    <!-- 二次確認彈窗：選取範圍（已選內/全部）× 階段 × 目標篩選（自動帶入列表當前篩選，可重選）+ model -->
    <a-modal
      v-model:visible="confirmOpen"
      title="確認初判歸因"
      ok-text="開始判決"
      cancel-text="取消"
      :width="1040"
      :ok-loading="running"
      @ok="doRun"
    >
      <div class="flex flex-col gap-3">
        <!-- 選取範圍：有勾選列才提供「已選內」；階段+篩選對兩種範圍皆生效（已選內＝在勾選列集合中再交集）-->
        <div v-if="runCount" class="flex items-center gap-2">
          <span class="text-xs text-gray-500">選取範圍</span>
          <a-radio-group v-model="targetMode" size="small" @change="refreshTargetCount">
            <a-radio value="selected">已選 {{ runCount }} 筆內</a-radio>
            <a-radio value="scope">全部資料</a-radio>
          </a-radio-group>
        </div>

        <div class="flex flex-col gap-3">
          <div>
            <div class="mb-1 text-xs text-gray-500">
              目標判決階段（預設只判未判；加選已判階段＝再判）
            </div>
            <a-checkbox-group v-model="targetStages" @change="refreshTargetCount">
              <a-checkbox v-for="(lbl, code) in STAGE_LABELS" :key="code" :value="code">
                {{ lbl }}
              </a-checkbox>
            </a-checkbox-group>
          </div>
          <!-- 目標篩選：共用 AttributionFilterBar（完整篩選欄，與列表對齊；自動帶入列表當前篩選，可重選）。
               星等/日期/ID 兩分支皆套；傾向/信心分層/L1 為判決級，僅對已判分支生效（見 usePrejudgeJob._scopeBody）。 -->
          <div>
            <div class="mb-1 text-xs text-gray-500">目標篩選（已自動帶入列表當前篩選，可重選）</div>
            <AttributionFilterBar
              :model="draftFilters"
              :fields="PREJUDGE_TARGET_FIELDS"
              :cascade-options="cascadeOptions"
              @change="refreshTargetCount"
            />
            <div class="mt-1 text-xs text-gray-400">
              星等 / 日期 / ID / 外部評論 對所有目標生效；傾向 / 信心分層 / L1
              僅對「已判」階段生效（未判列尚無判決可比對）。
            </div>
          </div>
          <!-- 再判信心範圍：勾選任一已判階段才顯示（原「再判收斂」的傾向/信心/L1 已併入上方統一篩選欄）-->
          <div v-if="hasJudgedStage" class="flex items-center gap-2">
            <span class="text-xs text-gray-500">再判信心範圍</span>
            <a-radio-group v-model="lowConfOnly" size="small" @change="refreshTargetCount">
              <a-radio :value="true">僅低信心</a-radio>
              <a-radio :value="false">全部信心</a-radio>
            </a-radio-group>
          </div>
        </div>

        <div class="text-sm text-[var(--color-text-1)]">
          將對 <b class="text-[rgb(var(--primary-6))]">{{ targetCount }}</b>
          筆進行初判歸因（正向不分類；負向與含問題點的中性評論歸 L1→L3）。
        </div>

        <div>
          <div class="mb-1 text-xs text-gray-500">
            LLM 模型配置（同「設定 › LLM 模型連線」；本次使用：<b>{{ currentLlmLabel || '未選' }}</b
            >）
          </div>
          <a-select
            v-model="llmConfigId"
            style="width: 100%"
            :options="LLM_OPTS"
            placeholder="選擇模型（預設啟用中）"
          />
        </div>
        <div class="text-xs text-gray-400">確認後開始批量判決，過程會消耗 token。</div>
      </div>
    </a-modal>

    <!-- 導出確認彈窗：草稿帶入列表當前篩選、可重選（共用 AttributionFilterBar）；有勾選則只導勾選列 -->
    <a-modal
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
        <!-- 輸出結果版本：與「判決模型」篩選（圈哪些評論）語義獨立——這裡決定輸出「哪個模型判的內容」 -->
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
              ：每則評論輸出「最近一次判決」的內容——不同評論可能由不同模型判出（判決模型欄可辨識）。
            </div>
            <div>
              <b class="font-medium text-gray-500">選特定模型</b>
              ：改輸出「該模型判過的版本」（取其最新一次），用於多模型結果對比；該模型沒判過的評論不會出現在檔案中，明細與統計表都會換成該模型的結果。
            </div>
            <div>
              <b class="font-medium text-gray-500">注意</b>
              ：下方「導出範圍篩選」一律以<b>當前判決</b>決定哪些評論入選（例：篩「負向」＝當前判決為負向），與此處選的輸出版本無關。
            </div>
          </div>
        </div>
        <div>
          <div class="mb-1 text-xs text-gray-500">導出範圍篩選（已帶入列表當前篩選，可重選）</div>
          <AttributionFilterBar
            :model="exportFilters"
            :fields="toolbarFields"
            :cascade-options="cascadeOptions"
            :model-options="modelOptions"
          />
        </div>
        <div class="text-xs text-gray-400">確認後於背景組檔，完成自動下載（可於進度條停止）。</div>
      </div>
    </a-modal>

    <!-- 操作欄：查看判決詳情抽屜（完整展示原文/關聯資料/每條歸因全欄位；抽出為獨立元件）-->
    <AttributionDetailDrawer v-model:visible="detailOpen" :row="detailRow" />

    <!-- 操作欄：標真值彈窗（級聯選 → LLM 評分把關 → 低信心填理由 → 標註；重判依 finding_id 保留）-->
    <a-modal
      v-model:visible="truelabelOpen"
      title="標註真值分類"
      :footer="false"
      :width="560"
      unmount-on-close
    >
      <div v-if="truelabelRow" class="flex flex-col gap-3">
        <div class="text-xs text-[var(--color-text-3)]">
          為每條歸因用級聯選正確分類（留空＝清除）。確認時 LLM 會對「該真值 vs
          反饋原文」評分並對比原判信心， 信心明顯下降需填修改理由。此標註供準確率評估，不改變 AI
          判決。
        </div>

        <div
          v-for="(a, ai) in truelabelRow.attributions || []"
          :key="ai"
          class="flex flex-col gap-1.5 rounded-md border border-[var(--color-neutral-3)] p-2.5"
        >
          <div class="text-xs text-[var(--color-text-2)]">AI 歸因：{{ attrPath(a) }}</div>
          <a-cascader
            v-if="a.finding_id"
            v-model="truelabelDraft[a.finding_id]"
            :options="cascadeOpts"
            :loading="cascadeLoading"
            :disabled="truelabelPhase === 'review'"
            size="small"
            allow-clear
            allow-search
            expand-trigger="hover"
            placeholder="級聯選正確分類（留空＝清除）"
          />

          <!-- review：LLM 前後信心對比 + 低信心必填理由 -->
          <template
            v-if="truelabelPhase === 'review' && a.finding_id && truelabelEvals[a.finding_id]"
          >
            <div class="text-xs" :class="evalDelta(truelabelEvals[a.finding_id]).cls">
              {{ evalDelta(truelabelEvals[a.finding_id]).text }}
            </div>
            <div
              v-if="truelabelEvals[a.finding_id].reason_llm"
              class="text-[11px] leading-snug text-[var(--color-text-3)]"
            >
              LLM 判讀：{{ truelabelEvals[a.finding_id].reason_llm }}
            </div>
            <a-textarea
              v-if="truelabelEvals[a.finding_id].reason_required"
              v-model="truelabelReasons[a.finding_id]"
              size="small"
              :auto-size="{ minRows: 2 }"
              placeholder="此真值 LLM 信心明顯偏低，請填寫修改理由（必填）"
              class="mt-0.5"
            />
          </template>
        </div>

        <!-- 兩階段 footer -->
        <div class="flex justify-end gap-2 pt-1">
          <template v-if="truelabelPhase === 'select'">
            <a-button size="small" @click="truelabelOpen = false">取消</a-button>
            <a-button
              type="primary"
              size="small"
              :loading="truelabelSaving"
              @click="evaluateTrueLabels"
            >
              評估並標註
            </a-button>
          </template>
          <template v-else>
            <a-button size="small" @click="truelabelPhase = 'select'">返回修改</a-button>
            <a-button
              type="primary"
              size="small"
              :loading="truelabelSaving"
              :disabled="reasonBlocked"
              @click="confirmTrueLabels"
            >
              確認標註
            </a-button>
          </template>
        </div>
      </div>
    </a-modal>

    <!-- 歸因備註彈窗：左右佈局 7:3——左＝時間軸歷史，右＝新增備註（與判決歷史彈窗同比例）-->
    <a-modal
      v-model:visible="noteOpen"
      title="歸因備註"
      :footer="false"
      :width="680"
      unmount-on-close
    >
      <div class="flex gap-5">
        <!-- 左：append-only 歷史時間軸（新到舊；佔 7/10）-->
        <div class="min-w-0 flex-[7]">
          <StateGuard :loading="noteLoading" error="">
            <!-- 滾動容器包在 a-timeline 外層（同 JudgmentHistoryModal）：timeline 是 flex column、
                 item min-height 78px，max-h+overflow 直掛 timeline 會被 flex-shrink 壓縮致內容堆疊。 -->
            <div v-if="noteList.length" class="max-h-[360px] overflow-auto">
              <a-timeline class="pl-1">
                <a-timeline-item v-for="n in noteList" :key="n.id">
                  <div
                    class="flex flex-wrap items-center gap-x-2 text-[11px] text-[var(--color-text-3)]"
                  >
                    <span class="font-medium text-[var(--color-text-2)]">{{ n.author }}</span>
                    <span>{{ fmtNoteTime(n.created_at) }}</span>
                  </div>
                  <div
                    class="mt-0.5 whitespace-pre-wrap text-xs leading-snug text-[var(--color-text-1)]"
                  >
                    {{ n.content }}
                  </div>
                </a-timeline-item>
              </a-timeline>
            </div>
            <a-empty v-else description="尚無備註" />
          </StateGuard>
        </div>
        <!-- 右：新增備註（flex-1 填滿剩餘寬）-->
        <div
          class="flex min-w-0 flex-[3] flex-col gap-2 border-l border-[var(--color-neutral-3)] pl-5"
        >
          <a-textarea
            v-model="noteDraft"
            :auto-size="{ minRows: 4 }"
            :max-length="500"
            show-word-limit
            placeholder="輸入備註內容（供覆核者間留言、追蹤同一問題）…"
          />
          <div class="flex justify-end">
            <a-button
              type="primary"
              size="small"
              :loading="noteSaving"
              :disabled="!noteDraft.trim()"
              @click="submitNote"
            >
              送出備註
            </a-button>
          </div>
        </div>
      </div>
    </a-modal>
  </div>
</template>

<style scoped>
/* 複合評論欄星等縮小：Arco a-rate 預設星 ~20px 過大，主列精巧化縮至 14px，與傾向 tag / 標題同行不搶高。
   :deep 觸及 Arco 內部 .arco-rate-character（utility / prop 無法觸及第三方深層 DOM）。 */
:deep(.review-rate .arco-rate-character) {
  font-size: 14px;
  margin-right: 2px;
}
/* 判決歸因合併欄：每條歸因一塊，塊間細線分隔（單欄內堆疊，無需跨欄等高，故不設 min-height）。 */
.verdict-blk {
  padding: 6px 0;
  border-bottom: 1px solid var(--color-neutral-3);
}
.verdict-blk:first-child {
  padding-top: 0;
}
.verdict-blk:last-child {
  border-bottom: none;
  padding-bottom: 0;
}
</style>

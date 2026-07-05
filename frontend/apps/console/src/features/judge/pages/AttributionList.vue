<script setup lang="ts">
/**
 * 歸因列表（伺服器端分頁 + 選擇驅動初判歸因 + 正負傾向 + 原始+歸因合表）。
 *
 * 分頁/篩選/排序皆走後端（/api/problems limit-offset；occurred_at DESC 穩定）；表頭固定、表身內滾動、
 * 底部完整 Arco 分頁。選取跨頁累積（複選 / 分頁選取 / 全部未判 scope）；導出走後端全量 CSV。
 * 正向/中性/傾向不明 不歸因，只有負向才有 L1→L3。
 *
 * 資料/篩選/選取/初判歸因/導出邏輯下沉 `useAttributionList`；欄位/篩選器/展開行明細依來源切換
 * 讀 `SOURCE_LIST_SCHEMAS`（product_reviews 已打樣，其餘來源沿用固定欄位 fallback）。
 */
import { computed, nextTick, onMounted, ref } from 'vue';
import { Message } from '@arco-design/web-vue';
import {
  IconCheck,
  IconClose,
  IconDownload,
  IconEdit,
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
import { ExportProgressBar, StateGuard, TableLayout } from '@/components';
import { composeLlmLabel } from '@/features/settings/utils';
import {
  ACTION_LABEL,
  ALL_PAGINATION,
  POLARITY_LABELS,
  SOURCES,
  STAGE_LABELS,
  STATUS_COLOR,
  STATUS_LABEL,
  TABLE_DEFAULTS,
  TIER_LABELS,
  TRAVELLER_TYPE_LABELS,
  type Attribution,
  type ProblemRow,
} from '../constants';
import { useAttributionList } from '../composables';
import { fmtDt } from '../utils';

const POLARITY_COLOR: Record<string, string> = {
  positive: 'green',
  negative: 'red',
  neutral: 'gray',
  unknown: 'orange',
};

/** 判決階段語義色（未判灰 / 已判決綠 / 待覆核橙 / 待數據補充藍 / 資訊不足灰）。 */
const STAGE_COLOR: Record<string, string> = {
  unjudged: 'gray',
  judged: 'green',
  pending_review: 'orange',
  pending_data: 'arcoblue',
  insufficient: 'gray',
};

const SOURCE_OPTS = SOURCES.map((s) => ({ value: s.value, label: s.label }));

const source = ref('product_reviews');

const {
  schema,
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
  llmConfigId,
  llmConfigs,
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
  targetPolarity,
  lowConfOnly,
  targetCount,
  hasJudgedStage,
  refreshTargetCount,
  doRun,
  pauseJob,
  resumeJob,
  cancelJob,
  exportCsv,
  exporting,
  exportStatus,
  exportProgress,
  exportPct,
  cancelExport,
  isRowBusy,
  rejudgeRow,
  reviewFinding,
  init,
} = useAttributionList(source);

// 單列重判完成 + 重載後，把表身捲回剛判的那一列（大列表·表身內滾動 y='100%'，重載會回頂 → 失去位置）。
const tableRef = ref<{ $el: HTMLElement } | null>(null);
const onRejudge = async (id: string) => {
  await rejudgeRow(id); // composable 內含 SSE 等待 + 重載本頁（同頁碼/排序 → 該列索引不變）
  await nextTick();
  const idx = rows.value.findIndex((r) => String(r._group) === id);
  if (idx < 0) return;
  const tr = tableRef.value?.$el?.querySelectorAll('.arco-table-body tbody > tr')[idx];
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
    return truelabelEvals.value[fid]?.reason_required && !(truelabelReasons.value[fid] || '').trim();
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
      attrs.map((a) => evaluateTrueLabel(a.finding_id as string, truelabelDraft.value[a.finding_id as string])),
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
    attrs.forEach((a) => (a.true_label = (truelabelDraft.value[a.finding_id as string] || '').trim() || undefined));
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

const LLM_OPTS = computed(() => llmConfigs.value.map((c) => ({ value: c.id, label: composeLlmLabel(c) })));

/** schema 是否含某篩選 type（控制篩選 UI 條件渲染）。 */
const hasFilter = (t: string) => schema.value.filters.some((f) => f.type === t);
/** 篩選下拉選項（由既有 label SSOT 衍生，勿另造）。 */
const POLARITY_OPTS = Object.entries(POLARITY_LABELS).map(([value, label]) => ({ value, label }));
const STAGE_OPTS = Object.entries(STAGE_LABELS).map(([value, label]) => ({ value, label }));
const TIER_OPTS = Object.entries(TIER_LABELS).map(([value, label]) => ({ value, label }));
const SCORE_OPTS = [1, 2, 3, 4, 5].map((v) => ({ value: v, label: `${v} 星` }));
/** L1 域選項（動態；label 附該域筆數輔助判斷）。 */
const L1_OPTS = computed(() =>
  l1Options.value.map((d) => ({ value: d.code, label: `${d.label}（${d.count}）` })),
);

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
  <!-- 初判歸因控制列送進固定工具列橫帶（tab 下方），與歸因縱覽一致、恆常可見 -->
  <Teleport to="#page-toolbar">
    <div class="flex items-center gap-3">
      <span class="text-sm text-gray-500">來源</span>
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
      <!-- 統一操作區：主行為 primary、次要 outline（見 rules/frontend-vue.md 按鈕規範）-->
      <a-button type="primary" size="small" :loading="running" @click="openPrejudge">
        初判歸因{{ runCount ? `（已選 ${runCount}）` : '' }}
      </a-button>
      <a-button size="small" type="outline" :loading="exporting" @click="exportCsv">
        <template #icon><icon-download /></template>
        導出列表{{ runCount ? `（已選 ${runCount}）` : '' }}
      </a-button>
    </div>
  </Teleport>

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
        <a-button
          v-if="jobStatus === 'paused'"
          size="small"
          type="primary"
          @click="resumeJob"
        >
          恢復
        </a-button>
        <a-button
          v-else
          size="small"
          :disabled="jobStatus === 'cancelling'"
          @click="pauseJob"
        >
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
      :title="`歸因列表（共 ${total} · 未判 ${unjudged}）`"
      hint="伺服器端分頁；勾選/分頁選取做初判歸因或導出"
    >
      <!-- 篩選維度列：傾向 / 判決階段 / 星等 / 信心分層 / L1 域（依 schema.filters 條件渲染）-->
      <div class="mb-2 flex flex-wrap items-center gap-2">
        <a-select
          v-if="hasFilter('polarity')"
          v-model="polarityFilter"
          size="small"
          allow-clear
          placeholder="傾向"
          style="width: 116px"
          :options="POLARITY_OPTS"
          @change="onFilterChange"
        />
        <a-select
          v-if="hasFilter('stage')"
          v-model="stageFilter"
          multiple
          size="small"
          :max-tag-count="1"
          placeholder="判決階段"
          style="width: 190px"
          :options="STAGE_OPTS"
          @change="onFilterChange"
        />
        <a-select
          v-if="hasFilter('score')"
          v-model="scoreFilter"
          multiple
          size="small"
          :max-tag-count="2"
          placeholder="星等"
          style="width: 170px"
          :options="SCORE_OPTS"
          @change="onFilterChange"
        />
        <a-select
          v-if="hasFilter('tier')"
          v-model="tierFilter"
          size="small"
          allow-clear
          placeholder="信心分層"
          style="width: 130px"
          :options="TIER_OPTS"
          @change="onFilterChange"
        />
        <a-select
          v-if="hasFilter('l1Domain')"
          v-model="l1Filter"
          size="small"
          allow-clear
          placeholder="L1 歸因域"
          style="width: 160px"
          :options="L1_OPTS"
          @change="onFilterChange"
        />
        <a-range-picker
          v-for="f in schema.filters.filter((x) => x.type === 'dateRange')"
          :key="f.type"
          v-model="dateRange"
          size="small"
          value-format="YYYY-MM-DD"
          style="width: 240px"
          :placeholder="[`${'label' in f ? f.label : ''}起`, `${'label' in f ? f.label : ''}迄`]"
          @change="onFilterChange"
        />
      </div>

      <!-- 精確查詢 + 分頁選取 + 操作（重置 / 展開收合；右側顯示生效篩選與選取計數）-->
      <div class="mb-2 flex flex-wrap items-center gap-2">
        <a-input
          v-model="prodOidFilter"
          size="small"
          allow-clear
          style="width: 140px"
          placeholder="商品 prod_oid"
          @press-enter="onFilterChange"
          @clear="onFilterChange"
        />
        <a-input
          v-model="orderOidFilter"
          size="small"
          allow-clear
          style="width: 140px"
          placeholder="訂單 order_oid"
          @press-enter="onFilterChange"
          @clear="onFilterChange"
        />
        <a-input
          v-model="pageSpec"
          size="small"
          allow-clear
          style="width: 190px"
          placeholder="分頁選取 如 1,2~5"
          @press-enter="selectPages"
        />
        <a-button size="small" @click="selectPages">選取分頁</a-button>
        <!-- 常駐可見以利發現「取消選擇」；無選取時 disabled（非 v-if 隱藏） -->
        <a-button size="small" :disabled="!runCount" @click="clearSelection">清除選擇</a-button>
        <div class="flex-1" />
        <span v-if="activeFilterCount" class="text-xs text-[rgb(var(--primary-6))]">
          已套用 {{ activeFilterCount }} 項篩選
        </span>
        <span class="text-xs text-gray-400">每頁 {{ pageSize }} · 已選 {{ runCount }}</span>
        <a-button size="small" type="outline" status="warning" @click="resetFilters">重置篩選</a-button>
      </div>
      <StateGuard
        :loading="loading"
        :error="error"
        :empty="!rows.length"
        empty-text="尚無資料，請先到「資料上傳」上傳 CSV"
      >
        <a-table
          ref="tableRef"
          v-bind="TABLE_DEFAULTS"
          :data="rows"
          :columns="COLS"
          :pagination="{ ...ALL_PAGINATION, current: page, pageSize, total }"
          :row-selection="{ type: 'checkbox', selectedRowKeys, showCheckedAll: true }"
          class="min-h-0 flex-1"
          row-key="_group"
          :scroll="{ x: SCROLL_X, y: '100%' }"
          @page-change="
            (p: number) => {
              page = p;
              loadPage();
            }
          "
          @page-size-change="
            (s: number) => {
              pageSize = s;
              page = 1;
              loadPage();
            }
          "
          @selection-change="onSelectionChange"
          @sorter-change="onSortChange"
        >
          <template #seq="{ record }">{{ record._seq }}</template>
          <!-- 反饋內容欄：星等+傾向+標題（第1行）／內容全文不省略（第2行）／#ID·時間（第3行）。 -->
          <template #review="{ record }">
            <div class="py-1">
              <div class="mb-0.5 flex flex-wrap items-center gap-x-2 gap-y-1">
                <a-rate
                  v-if="record.score !== null && record.score !== undefined && record.score !== ''"
                  :model-value="Number(record.score) || 0"
                  readonly
                  :count="5"
                  class="review-rate"
                />
                <a-tag v-if="record.polarity" size="small" :color="POLARITY_COLOR[record.polarity]">
                  {{ POLARITY_LABELS[record.polarity] || record.polarity }}
                </a-tag>
                <span v-else class="text-xs text-gray-300">未判</span>
                <span v-if="record.title" class="text-sm font-medium text-[var(--color-text-1)]">
                  {{ record.title }}
                </span>
              </div>
              <div v-if="record.content" class="whitespace-pre-wrap text-xs leading-relaxed text-[var(--color-text-2)]">
                {{ record.content }}
              </div>
              <div class="mt-0.5 text-[11px] text-[var(--color-text-3)]">
                #{{ record.source_record_id || record.source_id || '—' }} · {{ fmtDt(record.occurred_at) || '—' }}
              </div>
            </div>
          </template>
          <!-- 判決歸因合併欄：每條歸因一塊（L1→L3 + 信心 + 分層 + 判決階段 全放一起），
               塊間細線分隔；多歸因並存時逐塊堆疊，資訊聚合、一眼看完整判決。 -->
          <template #verdict="{ record }">
            <template v-if="record.attributions && record.attributions.length">
              <div v-for="(a, ai) in record.attributions" :key="ai" class="verdict-blk text-xs leading-relaxed">
                <!-- 反饋摘要（content.summary）置頂顯明：大家看得最多，作為該歸因的標題性內容（僅有值才顯示）-->
                <div
                  v-if="a.content?.summary"
                  class="text-[13px] font-semibold leading-snug text-[var(--color-text-1)]"
                >
                  <span class="mr-1 align-[1px] text-[11px] font-normal text-[var(--color-text-3)]">摘要</span>
                  {{ a.content.summary }}
                </div>
                <!-- L1→L3 純文字麵包屑（分類，次級）：L1 域藍字、› 灰分隔、L2/L3 中色 -->
                <div class="text-xs" :class="{ 'mt-0.5': a.content?.summary }">
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
                </div>
                <div class="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1">
                  <span class="text-[var(--color-text-3)]">信心</span>
                  <!-- 信心按 tier 上色：綠可採信 / 琥珀需覆核 / 紅必人工（< 0.8 需人工覆核）-->
                  <span class="font-semibold" :class="confClass(a.confidence?.tier)">
                    {{ typeof a.confidence?.value === 'number' ? a.confidence.value.toFixed(2) : '—' }}
                  </span>
                  <span
                    class="rounded bg-[var(--color-fill-2)] px-1.5 py-0.5 text-[var(--color-text-2)]"
                  >
                    {{ a.confidence?.tier ? TIER_LABELS[a.confidence.tier] || a.confidence.tier : '—' }}
                  </span>
                  <a-tag v-if="a.stage" size="small" :color="STAGE_COLOR[a.stage]">
                    {{ STAGE_LABELS[a.stage] || a.stage }}
                  </a-tag>
                  <!-- 人工覆核徽章：status≠new/空 時顯示（人工軸，與 AI 階段並存）-->
                  <a-tag
                    v-if="a.status && a.status !== 'new'"
                    size="small"
                    :color="STATUS_COLOR[a.status]"
                    bordered
                  >
                    {{ STATUS_LABEL[a.status] || a.status }}
                  </a-tag>
                  <!-- 真值標註徽章 -->
                  <a-tooltip v-if="a.true_label" :content="`真值：${a.true_label}`">
                    <span class="text-[11px] text-[rgb(var(--success-6))]">✔真值</span>
                  </a-tooltip>
                </div>
                <!-- 每條歸因分開操作，語義色區分：確認＝採信(綠) / 忽略＝駁回(紅) / 標真值＝輔助標註(text)。
                     選中該覆核狀態時填色(primary)，未選為 outline，一眼看出當前判定。 -->
                <div class="mt-1.5 flex flex-wrap items-center gap-1.5">
                  <a-button
                    size="mini"
                    status="success"
                    :type="a.status === 'confirmed' ? 'primary' : 'outline'"
                    @click="reviewFinding(a, 'confirmed')"
                  >
                    <template #icon><IconCheck /></template>
                    確認
                  </a-button>
                  <a-button
                    size="mini"
                    status="danger"
                    :type="a.status === 'dismissed' ? 'primary' : 'outline'"
                    @click="reviewFinding(a, 'dismissed')"
                  >
                    <template #icon><IconClose /></template>
                    忽略
                  </a-button>
                  <a-button size="mini" type="text" @click="openTrueLabel(record)">
                    <template #icon><IconEdit /></template>
                    標真值
                  </a-button>
                  <a-button
                    v-if="a.finding_id"
                    size="mini"
                    type="text"
                    @click="openNotes(a.finding_id)"
                  >
                    <template #icon><IconMessage /></template>
                    備註
                  </a-button>
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
                <span class="min-w-[3rem] shrink-0 rounded bg-[var(--color-fill-2)] px-1.5 py-0.5 text-center text-[11px] font-medium text-[var(--color-text-2)]">訂單</span>
                <div class="min-w-0">
                  <div class="font-medium text-[var(--color-text-1)]">{{ cell(record.order_mid) }}</div>
                  <div class="text-[var(--color-text-2)]">
                    OID {{ cell(record.order_oid) }} · 出發 {{ fmtDt(record.go_date, true) || '—' }}
                  </div>
                </div>
              </div>
              <!-- 商品 -->
              <div class="flex gap-1.5">
                <span class="min-w-[3rem] shrink-0 rounded bg-[var(--color-fill-2)] px-1.5 py-0.5 text-center text-[11px] font-medium text-[var(--color-text-2)]">商品</span>
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
                <span class="min-w-[3rem] shrink-0 rounded bg-[var(--color-fill-2)] px-1.5 py-0.5 text-center text-[11px] font-medium text-[var(--color-text-2)]">方案</span>
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
                <span class="min-w-[3rem] shrink-0 rounded bg-[var(--color-fill-2)] px-1.5 py-0.5 text-center text-[11px] font-medium text-[var(--color-text-2)]">供應商</span>
                <span class="text-[var(--color-text-1)]">{{ cell(record.supplier_oid) }}</span>
              </div>
              <!-- 旅客 -->
              <div class="flex items-center gap-1.5">
                <span class="min-w-[3rem] shrink-0 rounded bg-[var(--color-fill-2)] px-1.5 py-0.5 text-center text-[11px] font-medium text-[var(--color-text-2)]">旅客</span>
                <a-tag v-if="record.traveller_type" size="small" color="arcoblue">
                  {{ TRAVELLER_TYPE_LABELS[String(record.traveller_type)] || record.traveller_type }}
                </a-tag>
                <span v-if="record.member_uuid" class="break-all text-[var(--color-text-2)]">
                  {{ record.member_uuid }}
                </span>
              </div>
            </div>
          </template>
          <!-- 操作欄：整列級動作全展開（歸因/重判 + 查看詳情）；per-歸因 覆核在判決歸因欄內。與批量選取解耦。 -->
          <template #actions="{ record }">
            <div class="flex flex-col items-stretch gap-1.5 py-1">
              <!-- 重判＝破壞性（AI 覆寫既有歸因 + 燒判決額度）→ 二次確認；首次「歸因」無覆寫，直接執行不製造確認疲勞 -->
              <a-popconfirm
                v-if="record.attributions && record.attributions.length"
                content="重判會用 AI 重新判決並覆寫此列現有歸因（人工真值標註保留），並消耗判決額度。確定重判？"
                ok-text="重判"
                cancel-text="取消"
                @ok="onRejudge(record._group)"
              >
                <a-button type="primary" size="small" :loading="isRowBusy(record._group)">
                  重判
                </a-button>
              </a-popconfirm>
              <a-button
                v-else
                type="primary"
                size="small"
                :loading="isRowBusy(record._group)"
                @click="onRejudge(record._group)"
              >
                歸因
              </a-button>
              <a-button
                size="small"
                type="outline"
                :disabled="!(record.attributions && record.attributions.length)"
                @click="viewDetail(record)"
              >
                查看詳情
              </a-button>
            </div>
          </template>
        </a-table>
      </StateGuard>
    </TableLayout>

    <!-- 二次確認彈窗：於此選 model 配置後才執行初判歸因 -->
    <a-modal
      v-model:visible="confirmOpen"
      title="確認初判歸因"
      ok-text="開始判決"
      cancel-text="取消"
      :ok-loading="running"
      @ok="doRun"
    >
      <div class="flex flex-col gap-3">
        <!-- 目標模式：有勾選列才提供「已選」；否則依判決階段選取 -->
        <a-radio-group
          v-if="runCount"
          v-model="targetMode"
          size="small"
          @change="refreshTargetCount"
        >
          <a-radio value="selected">已選 {{ runCount }} 筆</a-radio>
          <a-radio value="scope">依判決階段選取</a-radio>
        </a-radio-group>

        <template v-if="targetMode === 'scope'">
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
          <!-- 再判收斂：勾選任一已判階段才顯示（預設負向 + 僅低信心）-->
          <div v-if="hasJudgedStage" class="flex items-center gap-4">
            <span class="text-xs text-gray-500">再判收斂</span>
            <a-select
              v-model="targetPolarity"
              size="small"
              style="width: 110px"
              allow-clear
              placeholder="傾向"
              :options="
                Object.entries(POLARITY_LABELS)
                  .filter(([k]) => k !== 'unknown')
                  .map(([value, label]) => ({ value, label }))
              "
              @change="refreshTargetCount"
            />
            <a-radio-group v-model="lowConfOnly" size="small">
              <a-radio :value="true">僅低信心</a-radio>
              <a-radio :value="false">全部信心</a-radio>
            </a-radio-group>
          </div>
        </template>

        <div class="text-sm text-[var(--color-text-1)]">
          將對 <b class="text-[rgb(var(--primary-6))]">{{ targetCount }}</b>
          筆進行初判歸因（正向不分類，只有負向歸 L1→L3）。
        </div>

        <div>
          <div class="mb-1 text-xs text-gray-500">LLM 模型配置（同「設定 › LLM 模型連線」）</div>
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

    <!-- 操作欄：查看判決詳情彈窗（該列每條歸因的完整依據；純前端，資料取自 row.attributions）-->
    <a-modal
      v-model:visible="detailOpen"
      :title="`判決詳情 · #${detailRow?.source_record_id ?? detailRow?.source_id ?? ''}`"
      :width="720"
      :footer="false"
      unmount-on-close
    >
      <div v-if="detailRow" class="flex flex-col gap-3">
        <div class="rounded-md bg-[var(--color-fill-1)] p-3 text-sm leading-relaxed text-[var(--color-text-1)]">
          {{ detailRow.content || '（無評論內容）' }}
        </div>
        <template v-if="detailRow.attributions && detailRow.attributions.length">
          <a-descriptions
            v-for="(a, ai) in detailRow.attributions"
            :key="ai"
            :title="`歸因 ${ai + 1}`"
            :column="1"
            size="small"
            bordered
            :label-style="{ width: '96px' }"
          >
            <a-descriptions-item label="歸因分類">{{ attrPath(a) }}</a-descriptions-item>
            <a-descriptions-item label="信心 / 分層">
              {{ typeof a.confidence?.value === 'number' ? a.confidence.value.toFixed(2) : '—' }} ·
              {{ a.confidence?.tier ? TIER_LABELS[a.confidence.tier] || a.confidence.tier : '—' }}
            </a-descriptions-item>
            <a-descriptions-item label="判決階段">
              {{ a.stage ? STAGE_LABELS[a.stage] || a.stage : '—' }}
            </a-descriptions-item>
            <!-- 反饋摘要（content.summary）；判決理由永遠＝同一 evidence 複製，故移除避免重複 -->
            <a-descriptions-item label="反饋摘要">{{ a.content?.summary || '—' }}</a-descriptions-item>
            <a-descriptions-item label="建議行動">
              {{ a.content?.action ? ACTION_LABEL[a.content.action] || a.content.action : '—' }}
            </a-descriptions-item>
          </a-descriptions>
        </template>
        <a-empty v-else description="此列尚無歸因（未判 / 正向不歸因）" />
      </div>
    </a-modal>

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
          為每條歸因用級聯選正確分類（留空＝清除）。確認時 LLM 會對「該真值 vs 反饋原文」評分並對比原判信心，
          信心明顯下降需填修改理由。此標註供準確率評估，不改變 AI 判決。
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
          <template v-if="truelabelPhase === 'review' && a.finding_id && truelabelEvals[a.finding_id]">
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
            <a-button size="small" @click="(truelabelPhase = 'select')">返回修改</a-button>
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

    <!-- 歸因備註彈窗：新增備註 + append-only 歷史（備註人 / 時間 / 內容）-->
    <a-modal v-model:visible="noteOpen" title="歸因備註" :footer="false" :width="520" unmount-on-close>
      <div class="flex flex-col gap-3">
        <div class="flex flex-col gap-2">
          <a-textarea
            v-model="noteDraft"
            :auto-size="{ minRows: 2 }"
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
        <div class="border-t border-[var(--color-neutral-3)] pt-2">
          <StateGuard :loading="noteLoading" error="">
            <div v-if="noteList.length" class="flex max-h-[320px] flex-col gap-2 overflow-auto">
              <div
                v-for="n in noteList"
                :key="n.id"
                class="rounded-md border border-[var(--color-neutral-3)] p-2.5"
              >
                <div class="flex items-center justify-between text-[11px] text-[var(--color-text-3)]">
                  <span class="font-medium text-[var(--color-text-2)]">{{ n.author }}</span>
                  <span>{{ fmtNoteTime(n.created_at) }}</span>
                </div>
                <div class="mt-1 whitespace-pre-wrap text-xs leading-snug text-[var(--color-text-1)]">
                  {{ n.content }}
                </div>
              </div>
            </div>
            <a-empty v-else description="尚無備註" />
          </StateGuard>
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

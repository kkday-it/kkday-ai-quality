<script setup lang="ts">
/**
 * 歸因列表（伺服器端分頁 + 選擇驅動初判歸因 + 正負傾向 + 原始+歸因合表）。
 *
 * 分頁/篩選/排序皆走後端（/api/problems limit-offset；occurred_at DESC 穩定）；表頭固定、表身內滾動、
 * 底部完整 Arco 分頁。選取跨頁累積（複選 / 分頁選取 / 全部未判 scope）；導出走後端全量 CSV。
 * 正向/中性/數據不足 不歸因，只有負向才有 L1→L3。
 *
 * 資料/篩選/選取/初判歸因/導出邏輯下沉 `useAttributionList`；欄位/篩選器/展開行明細依來源切換
 * 讀 `SOURCE_LIST_SCHEMAS`（product_reviews 已打樣，其餘來源沿用固定欄位 fallback）。
 */
import { computed, onMounted, ref } from 'vue';
import { IconDownload } from '@arco-design/web-vue/es/icon';
import { StateGuard, TableLayout } from '@/components';
import { composeLlmLabel } from '@/features/settings/utils';
import {
  ALL_PAGINATION,
  POLARITY_LABELS,
  SOURCES,
  TABLE_DEFAULTS,
  TIER_LABELS,
  TRAVELLER_TYPE_LABELS,
  type ProblemRow,
} from '../constants';
import { fmtDt, useAttributionList } from '../composables';

const POLARITY_COLOR: Record<string, string> = {
  positive: 'green',
  negative: 'red',
  neutral: 'gray',
  unknown: 'orange',
};

const SOURCE_OPTS = SOURCES.map((s) => ({ value: s.value, label: s.label }));

/** 依傾向給整列一個 class，用背景色一眼區分正負中性/數據不足（未判無色）。 */
const rowClass = (record: ProblemRow) => (record.polarity ? `pol-row-${record.polarity}` : '');

const source = ref('product_reviews');

const {
  schema,
  polarityFilter,
  onlyProblem,
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
  selectedKeys,
  runCount,
  clearSelection,
  pageSpec,
  selectPages,
  running,
  progress,
  progressPct,
  costText,
  confirmOpen,
  pendingScope,
  runSelected,
  runAll,
  doRun,
  exportCsv,
  init,
} = useAttributionList(source);

const LLM_OPTS = computed(() => llmConfigs.value.map((c) => ({ value: c.id, label: composeLlmLabel(c) })));

/** 序號欄（前置於業務欄前）：依當前頁碼 + 列索引計算全域序號。 */
const SEQ_COL = { title: '序號', slotName: 'seq', width: 64 };
/** 目前來源欄位（序號欄 + schema 業務欄）。 */
const COLS = computed(() => [SEQ_COL, ...schema.value.columns]);

/** 展開行明細值：無值防禦式顯示「—」；時間欄依 format 正規化。 */
function expandFieldText(record: ProblemRow, key: string, format?: 'datetime' | 'date'): string {
  const v = record?.[key];
  if (v === null || v === undefined || v === '') return '—';
  if (format === 'datetime') return fmtDt(v) || '—';
  if (format === 'date') return fmtDt(v, true) || '—';
  return String(v);
}

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
      <a-button type="primary" size="small" :loading="running" @click="runSelected">
        進行初判歸因（已選 {{ runCount }}）
      </a-button>
      <a-button size="small" :loading="running" @click="runAll">
        全部未判（{{ unjudged }}）
      </a-button>
      <a-button size="small" @click="exportCsv">
        <template #icon><icon-download /></template>
        導出 CSV（{{ runCount ? `已選 ${runCount}` : '全部篩選' }}）
      </a-button>
    </div>
  </Teleport>

  <div class="flex h-full flex-col gap-4">
    <!-- 初判歸因進度：批量判決進行中才顯示（控制列已移入工具列橫帶）-->
    <div v-if="running" class="rounded-md border border-[#f0f0f0] bg-white px-4 py-3">
      <a-progress
        :percent="progressPct / 100"
        :status="progressPct >= 100 ? 'success' : 'normal'"
      />
      <div class="mt-1 flex flex-wrap gap-x-4 text-xs text-gray-500">
        <span>已處理 {{ progress.processed }} / {{ progress.total }} 筆…</span>
        <span v-if="costText">花費 {{ costText }}</span>
      </div>
    </div>

    <TableLayout
      :title="`歸因列表（共 ${total} · 未判 ${unjudged}）`"
      hint="伺服器端分頁；勾選/分頁選取做初判歸因或導出"
    >
      <!-- 篩選列 1：傾向 + 排序 + 操作（重置 / 一鍵收合）-->
      <div class="mb-2 flex flex-wrap items-center gap-3">
        <template v-if="schema.filters.some((f) => f.type === 'polarity')">
          <a-checkbox v-model="onlyProblem" @change="onFilterChange">僅看問題（負向）</a-checkbox>
          <a-select
            v-model="polarityFilter"
            size="small"
            style="width: 130px"
            :disabled="onlyProblem"
            :options="[
              { value: '', label: '全部傾向' },
              { value: 'negative', label: POLARITY_LABELS.negative },
              { value: 'positive', label: POLARITY_LABELS.positive },
              { value: 'neutral', label: POLARITY_LABELS.neutral },
              { value: 'unknown', label: POLARITY_LABELS.unknown },
            ]"
            @change="onFilterChange"
          />
        </template>
        <a-button size="small" @click="resetFilters">重置篩選</a-button>
        <a-button size="small" @click="toggleExpandAll">
          {{ allExpanded ? '一鍵收合' : '全部展開' }}
        </a-button>
      </div>

      <!-- 篩選列 2：商品垂直分類 + 日期 + 商品/訂單 ID + 分頁選取（一行）-->
      <div class="mb-2 flex flex-wrap items-center gap-3">
        <template v-for="f in schema.filters" :key="f.type">
          <!-- 商品垂直分類篩選（多選；選項來自 config 動態解析）-->
          <a-select
            v-if="f.type === 'productVertical'"
            v-model="productVerticalFilter"
            multiple
            size="small"
            style="min-width: 180px"
            placeholder="商品垂直分類"
            :max-tag-count="2"
            :options="productVerticalOpts.map((g) => ({ value: g, label: g }))"
            @change="onFilterChange"
          />
          <!-- 日期區間篩選 -->
          <a-range-picker
            v-else-if="f.type === 'dateRange'"
            v-model="dateRange"
            size="small"
            value-format="YYYY-MM-DD"
            style="width: 240px"
            :placeholder="[`${f.label}起`, `${f.label}迄`]"
            @change="onFilterChange"
          />
        </template>
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
          style="width: 200px"
          placeholder="如 1,2,3 或 1,2~5 或 1~5"
          @press-enter="selectPages"
        />
        <a-button size="small" @click="selectPages">選取分頁</a-button>
        <a-button v-if="runCount" size="small" @click="clearSelection">清除選擇</a-button>
        <span class="text-xs text-gray-400">每頁 {{ pageSize }} · 已選 {{ runCount }}</span>
      </div>
      <StateGuard
        :loading="loading"
        :error="error"
        :empty="!rows.length"
        empty-text="尚無資料，請先到「資料上傳」上傳 CSV"
      >
        <a-table
          v-bind="TABLE_DEFAULTS"
          v-model:expanded-keys="expandedKeys"
          :data="rows"
          :columns="COLS"
          :pagination="{ ...ALL_PAGINATION, current: page, pageSize, total }"
          :row-selection="{ type: 'checkbox', selectedRowKeys: selectedKeys, showCheckedAll: true }"
          :expandable="{}"
          :row-class="rowClass"
          class="min-h-0 flex-1"
          row-key="item_id"
          :scroll="{ y: '100%' }"
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
          @selection-change="(keys) => (selectedKeys = keys.map(String))"
          @sorter-change="onSortChange"
        >
          <template #seq="{ rowIndex }">{{ (page - 1) * pageSize + rowIndex + 1 }}</template>
          <template #occurred="{ record }">{{ fmtDt(record.occurred_at) }}</template>
          <template #godate="{ record }">{{ fmtDt(record.go_date, true) }}</template>
          <template #pol="{ record }">
            <a-tag v-if="record.polarity" size="small" :color="POLARITY_COLOR[record.polarity]">
              {{ POLARITY_LABELS[record.polarity] || record.polarity }}
            </a-tag>
            <span v-else class="text-gray-300">未判</span>
          </template>
          <template #attr="{ record }">
            <div v-if="record.l1_label || record.l3_label" class="text-xs leading-relaxed">
              <div><span class="text-gray-400">L1</span> {{ record.l1_label }}</div>
              <div v-if="record.l2_label">
                <span class="text-gray-400">L2</span> {{ record.l2_label }}
              </div>
              <div v-if="record.l3_label">
                <span class="text-gray-400">L3</span> {{ record.l3_label }}
              </div>
            </div>
            <span v-else class="text-gray-300">—</span>
          </template>
          <template #tier="{ record }">
            <span v-if="record.confidence_tier">{{
              TIER_LABELS[record.confidence_tier] || record.confidence_tier
            }}</span>
            <span v-else class="text-gray-300">—</span>
          </template>
          <!-- 展開行明細：依 schema.expandGroups 分區（每組一個帶標題 a-descriptions），預設全展開可收合 -->
          <template #expand-row="{ record }">
            <a-descriptions
              v-for="(g, gi) in schema.expandGroups"
              :key="gi"
              class="attr-expand"
              :class="{ 'mt-2': gi > 0 }"
              :title="g.title"
              :column="g.column || 4"
              size="small"
              bordered
              :label-style="{ width: '88px' }"
            >
              <a-descriptions-item
                v-for="f in g.fields"
                :key="f.key"
                :span="f.span || 1"
                :label="f.label"
              >
                <a-rate
                  v-if="f.kind === 'rate'"
                  :model-value="Number(record.score) || 0"
                  readonly
                  :count="5"
                />
                <template v-else-if="f.kind === 'traveller'">
                  {{
                    TRAVELLER_TYPE_LABELS[String(record.traveller_type ?? '')] ??
                    record.traveller_type ??
                    '—'
                  }}
                </template>
                <template v-else>{{ expandFieldText(record, f.key, f.format) }}</template>
              </a-descriptions-item>
            </a-descriptions>
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
        <div class="text-sm text-[var(--color-text-1)]">
          將對
          <b class="text-[rgb(var(--primary-6))]">{{
            pendingScope === 'all' ? `全部未判 ${unjudged}` : `已選 ${runCount}`
          }}</b>
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
  </div>
</template>

<style scoped>
/* 傾向背景色（一眼區分正負中性/數據不足）：row-class 由 rowClass() 給出，
   Arco 內部 tr/td 無法用 utility 觸及，故用 :deep + Arco 色階 token（非 --kk- DS token）。 */
:deep(.arco-table-tr.pol-row-negative > .arco-table-td) {
  background-color: rgb(var(--red-1));
}
:deep(.arco-table-tr.pol-row-positive > .arco-table-td) {
  background-color: rgb(var(--green-1));
}
:deep(.arco-table-tr.pol-row-neutral > .arco-table-td) {
  background-color: rgb(var(--gray-2));
}
:deep(.arco-table-tr.pol-row-unknown > .arco-table-td) {
  background-color: rgb(var(--orange-1));
}

/* 展開明細固定欄寬：Arco a-descriptions 內部為 table，預設依內容自動撐欄→每列寬度不一。
   table-layout:fixed + 固定 label 寬，讓 3 組 label/value 欄寬跨列一致（utility/prop 無法觸及內部 table）。 */
:deep(.attr-expand .arco-descriptions-table) {
  table-layout: fixed;
}
:deep(.attr-expand .arco-descriptions-item-value) {
  word-break: break-word;
}
</style>

<script setup lang="ts">
/**
 * 歸因歷史抽屜：每次觸發 LLM 歸因（批量初判 / 選取多筆 / 單筆重新初判）一列。
 *
 * 列表走 /v1/prejudge/runs（server 分頁；執行中列由後端 overlay 即時進度），展開行懶載
 * /v1/prejudge/runs/{job_id} 取 per-stage LLM 用量明細（呼叫數 / token / reasoning / 快取 / 費用）
 * 與發起參數快照。開啟且有執行中 run 時每 3s 自動刷新（useIntervalFn）。
 */
import { computed, reactive, ref, watch } from 'vue';
import { useIntervalFn } from '@vueuse/core';
import { getPrejudgeRun, listPrejudgeRuns, type PrejudgeRun, type PrejudgeRunStage } from '@/api';
import { TableLayout } from '@/components';
import { DEFAULT_PAGE_SIZE, SOURCE_LABEL } from '../constants';
import { fmtDt } from '../utils';

const props = defineProps<{ visible: boolean }>();
const emit = defineEmits<{ (e: 'update:visible', v: boolean): void }>();

/** 觸發型態 → 顯示 label / tag 色（三型分色一眼可辨；紫留給「重新初判」）。 */
const KIND_LABEL: Record<string, string> = { batch: '批量', selected: '選取', single: '單筆' };
const KIND_COLOR: Record<string, string> = { batch: 'arcoblue', selected: 'gold', single: 'cyan' };
const STATUS_LABEL: Record<string, string> = {
  running: '執行中',
  paused: '已暫停',
  cancelling: '停止中',
  done: '完成',
  error: '失敗',
  cancelled: '已停止',
  interrupted: '已中斷',
};
const STATUS_COLOR: Record<string, string> = {
  running: 'arcoblue',
  paused: 'orange',
  cancelling: 'orange',
  done: 'green',
  error: 'red',
  cancelled: 'gray',
  interrupted: 'red',
};

const loading = ref(false);
const error = ref('');
const rows = ref<PrejudgeRun[]>([]);
const total = ref(0);
const page = ref(1);
const pageSize = ref(DEFAULT_PAGE_SIZE);
// 展開行懶載的詳情快取（job_id → stages+params；失敗存 error 字串供行內顯示）
const details = reactive<Record<string, { stages: PrejudgeRunStage[] } | { error: string }>>({});

const load = async () => {
  loading.value = true;
  error.value = '';
  try {
    const data = await listPrejudgeRuns({
      limit: pageSize.value,
      offset: (page.value - 1) * pageSize.value,
    });
    rows.value = data.items;
    total.value = data.total;
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    loading.value = false;
  }
};

// 有執行中 run 且抽屜開著 → 每 3s 自動刷新（暫停/停止中也算未收斂）
const hasLive = computed(() =>
  rows.value.some((r) => ['running', 'paused', 'cancelling'].includes(r.status)),
);
const { pause, resume } = useIntervalFn(load, 3000, { immediate: false });
watch(
  () => props.visible && hasLive.value,
  (on) => (on ? resume() : pause()),
  { immediate: true },
);
watch(
  () => props.visible,
  (v) => {
    if (v) {
      page.value = 1;
      void load();
    }
  },
);

/** 展開行時懶載詳情（per-stage 明細）；已載過不重打。 */
const onExpand = async (rowKey: string | number) => {
  const id = String(rowKey);
  if (details[id]) return;
  try {
    details[id] = { stages: (await getPrejudgeRun(id)).stages };
  } catch (e) {
    details[id] = { error: e instanceof Error ? e.message : String(e) };
  }
};

/** 展開行詳情取值 helper（模板免型別窄化 cast）。 */
const stagesOf = (id: string): PrejudgeRunStage[] | null => {
  const d = details[id];
  return d && 'stages' in d ? d.stages : null;
};
const detailErrorOf = (id: string): string => {
  const d = details[id];
  return d && 'error' in d ? d.error : '';
};

const fmtTokens = (n: number | null) => (n == null ? '—' : n.toLocaleString());
const fmtCost = (v: number | null) => (v == null ? '—' : `$${v.toFixed(4)}`);

/** run 耗時（終態＝迄-起；執行中＝至今）；<1s 顯示 <1s。 */
const fmtDuration = (r: PrejudgeRun) => {
  const end = r.finished_at ? new Date(r.finished_at).getTime() : Date.now();
  const sec = Math.round((end - new Date(r.started_at).getTime()) / 1000);
  if (sec < 1) return '<1s';
  if (sec < 60) return `${sec}s`;
  return `${Math.floor(sec / 60)}m${sec % 60}s`;
};

/** 發起參數快照 → 人話摘要（只列有值且對追溯有意義的鍵）。 */
const paramsSummary = (r: PrejudgeRun) => {
  const p = r.params || {};
  const parts: string[] = [];
  if (Array.isArray(p.stages) && p.stages.length)
    parts.push(`階段：${(p.stages as string[]).join('、')}`);
  if (Array.isArray(p.product_verticals) && p.product_verticals.length)
    parts.push(`垂直分類：${(p.product_verticals as string[]).join('、')}`);
  if (p.target_polarity) parts.push(`傾向：${String(p.target_polarity)}`);
  if (p.max_confidence != null) parts.push(`信心上限：${String(p.max_confidence)}`);
  if (Array.isArray(p.item_ids) && p.item_ids.length)
    parts.push(`標的：${(p.item_ids as string[]).join('、')}`);
  if (p.backfilled) parts.push('（歷史回填自 llm_usage：成功/失敗數當時未記）');
  return parts.join('　');
};
</script>

<template>
  <a-drawer
    :visible="visible"
    :width="980"
    :footer="false"
    unmount-on-close
    @update:visible="(v: boolean) => emit('update:visible', v)"
  >
    <template #title>初判歷史（每次批量 / 選取 / 單筆重新初判的 LLM 使用紀錄）</template>

    <!-- 內建表格模式：滿高首尾固定 + server 分頁（含「全部」）+ error 表上方顯示 -->
    <TableLayout
      v-model:page="page"
      v-model:page-size="pageSize"
      full-height
      row-key="job_id"
      :data="rows"
      :loading="loading"
      :error="error"
      pagination="with-all"
      server
      :total="total"
      :expandable="{ width: 32 }"
      @change="load"
      @expand="onExpand"
    >
      <template #columns>
        <a-table-column title="開始時間" :width="150">
          <template #cell="{ record }">{{ fmtDt(record.started_at) }}</template>
        </a-table-column>
        <a-table-column title="類型" :width="112">
          <template #cell="{ record }">
            <a-tag size="small" :color="KIND_COLOR[record.kind] || 'gray'">{{
              KIND_LABEL[record.kind] || record.kind
            }}</a-tag>
            <a-tag v-if="record.rejudge" size="small" color="purple" class="ml-1">重新初判</a-tag>
          </template>
        </a-table-column>
        <a-table-column title="反饋來源" :width="96">
          <template #cell="{ record }">{{
            SOURCE_LABEL[record.source] || record.source || '—'
          }}</template>
        </a-table-column>
        <a-table-column title="模型" data-index="model" :width="120" ellipsis tooltip />
        <a-table-column title="筆數（成功/失敗）" :width="140">
          <template #cell="{ record }">
            {{ record.processed ?? 0 }} / {{ record.total }}
            <!-- 回填的歷史 run 當時未記成功/失敗 → 顯示 —，不誤報 0 -->
            <span class="text-xs text-gray-400"
              >（{{ record.ok ?? '—' }}✓ {{ record.failed ?? '—' }}✗）</span
            >
          </template>
        </a-table-column>
        <a-table-column title="Tokens" :width="100">
          <template #cell="{ record }">{{ fmtTokens(record.total_tokens) }}</template>
        </a-table-column>
        <a-table-column title="費用" :width="90">
          <template #cell="{ record }">{{ fmtCost(record.cost_usd) }}</template>
        </a-table-column>
        <a-table-column title="狀態" :width="92">
          <template #cell="{ record }">
            <a-tag size="small" :color="STATUS_COLOR[record.status] || 'gray'">
              {{ STATUS_LABEL[record.status] || record.status }}
            </a-tag>
          </template>
        </a-table-column>
        <a-table-column title="耗時" :width="80">
          <template #cell="{ record }">{{ fmtDuration(record) }}</template>
        </a-table-column>
        <a-table-column title="觸發人" data-index="triggered_by" :width="150" ellipsis tooltip />
      </template>

      <!-- 展開行：發起參數摘要 + per-stage LLM 用量明細（懶載） -->
      <template #expand-row="{ record }">
        <div class="px-2 py-1 text-xs">
          <div v-if="paramsSummary(record)" class="mb-2 text-gray-500">
            {{ paramsSummary(record) }}
          </div>
          <template v-if="details[record.job_id]">
            <a-alert v-if="detailErrorOf(record.job_id)" type="warning">
              明細載入失敗：{{ detailErrorOf(record.job_id) }}
            </a-alert>
            <a-table
              v-else-if="stagesOf(record.job_id)?.length"
              row-key="stage"
              :data="stagesOf(record.job_id) ?? []"
              :pagination="false"
              size="mini"
            >
              <template #columns>
                <a-table-column title="階段" data-index="stage" :width="110" />
                <a-table-column title="呼叫數" data-index="calls" :width="80" />
                <a-table-column title="輸入 tokens" :width="110">
                  <template #cell="{ record: s }">{{ fmtTokens(s.prompt_tokens) }}</template>
                </a-table-column>
                <a-table-column title="快取命中" :width="100">
                  <template #cell="{ record: s }">{{ fmtTokens(s.cached_tokens) }}</template>
                </a-table-column>
                <a-table-column title="輸出 tokens" :width="110">
                  <template #cell="{ record: s }">{{ fmtTokens(s.completion_tokens) }}</template>
                </a-table-column>
                <a-table-column title="其中 reasoning" :width="120">
                  <template #cell="{ record: s }">{{ fmtTokens(s.reasoning_tokens) }}</template>
                </a-table-column>
                <a-table-column title="費用" :width="90">
                  <template #cell="{ record: s }">{{ fmtCost(s.cost_usd) }}</template>
                </a-table-column>
              </template>
            </a-table>
            <div v-else class="text-gray-400">
              尚無 per-stage 明細（job 結束後彙整；stub / 零標的 run 無 LLM 呼叫）
            </div>
          </template>
          <a-spin v-else :size="14" />
        </div>
      </template>
    </TableLayout>
  </a-drawer>
</template>

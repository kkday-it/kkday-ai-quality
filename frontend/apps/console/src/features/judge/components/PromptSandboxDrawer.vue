<script setup lang="ts">
/**
 * 歸因列表「Prompt 測試」沙盒抽屜：對單列、勾選多筆、或依條件批量選取，跑使用者勾選的 7 條
 * prompt 子集（polarity + C-1..C-6）→ 逐筆逐 prompt 結果。**ungated**（不受正式歸因閘門限制，
 * 即使整體判正向也能測域 prompt）；測試歷史與正式初判完全分離（獨立 `prompt_sandbox_runs` 表），
 * 且捕捉完整 LLM log 供歷史回看（見 `sandbox_classify`/`prompt_sandbox.py`）。實時 log 串流為
 * Phase 2（此版本先留歷史回看，不做即時分頁）。
 *
 * scope='all'（工具列「依條件批量」入口）時的目標選取比照初判分類（`usePrejudgeJob`），委派
 * `usePromptSandboxTargets` 復用同一套 stage 驅動 + 篩選草稿 + 即時筆數預覽 pattern。
 */
import { computed, onMounted, ref, watch } from 'vue';
import { Message } from '@arco-design/web-vue';
import {
  getPromptSandboxRun,
  getPromptSandboxStatus,
  listPromptSandboxRuns,
  startPromptSandbox,
  type PromptSandboxItemResult,
  type PromptSandboxRunSummary,
} from '@/api';
import { useJudgeRulesStore } from '@/stores/judgeRules.store';
import { fmtDt } from '../utils';
import type { ProblemRow } from '../constants/source-schema.constant';
import type { CascadeNode } from '@/api';
// 相對路徑 import（非走 barrel）：本檔自身即為 components barrel 的一員，經 barrel 迴繞 import
// 同資料夾元件會觸發 circular dep（見 barrel-exports 規則）。
import AttributionFilterBar from './AttributionFilterBar.vue';
import { STAGE_LABELS, type FilterField } from '../constants';
import { usePromptSandboxTargets } from '../composables/usePromptSandboxTargets';
import type { PrejudgeListFilters } from '../composables/usePrejudgeJob';

const props = defineProps<{
  /** 是否顯示。 */
  visible: boolean;
  /** 當前反饋來源 code（product_reviews…）。 */
  source: string;
  /** 觸發入口：single＝單列按鈕；selection＝工具列對勾選多筆；all＝工具列依條件批量選取。 */
  scope: 'single' | 'selection' | 'all';
  /** 受測 source_id 清單（single/selection 顯式；all 時由內部依條件解析，此 prop 忽略）。 */
  sourceIds: string[];
  /** scope=single 時的目標列（供顯示評論原文預覽）。 */
  row?: ProblemRow | null;
  /** scope='all' 目標選取依賴：生效商品垂直分類。 */
  effVerticals?: string[];
  /** scope='all' 目標選取依賴：跨頁累積勾選（targetMode='selected' 時 within_ids 交集）。 */
  selectedKeys?: string[];
  /** scope='all' 目標選取依賴：頁面當前列表篩選快照（開選取器自動帶入草稿初值）。 */
  listFilters?: PrejudgeListFilters;
  /** scope='all' 目標篩選欄的歸因分類級聯選項（taxonomy 欄必給，來自 getTaxonomyCascade）。 */
  cascadeOptions?: CascadeNode[];
}>();

const emit = defineEmits<{ (e: 'update:visible', v: boolean): void }>();

// 從 judgeRules store 取 7 支 prompt 的 rule_code + 中文名（SSOT，免另存一份標籤）。
const store = useJudgeRulesStore();
const selectedCodes = ref<string[]>([]);
onMounted(async () => {
  if (!store.metas.length) await store.loadList();
});
const promptOptions = computed(() =>
  store.metas
    .filter((m) => m.rule_code.startsWith('prompt_'))
    .map((m) => ({ value: m.rule_code, label: store.labelFor(m.rule_code) }))
    .sort((a, b) =>
      a.value === 'prompt_polarity'
        ? -1
        : b.value === 'prompt_polarity'
          ? 1
          : a.value.localeCompare(b.value),
    ),
);
/** rule_code（prompt_C-3）→ 端點值（C-3 / polarity）。 */
const toPromptArg = (code: string): string => code.replace('prompt_', '');
const promptArgs = computed(() => selectedCodes.value.map(toPromptArg));

// ── scope='all' 依條件批量選取（比照初判分類 usePrejudgeJob 的目標選取 pattern）──
const targets = usePromptSandboxTargets({
  source: () => props.source,
  effVerticals: () => props.effVerticals,
  selectedKeys: () => props.selectedKeys ?? [],
  listFilters: () => props.listFilters ?? {},
});
/** 依條件批量選取的目標篩選欄位子集（順序對齊初判分類 PREJUDGE_TARGET_FIELDS；
 * 判決階段由上方 checkbox 承擔，不納入此欄）。 */
const TARGET_FIELDS: FilterField[] = [
  'recOid',
  'prodOid',
  'orderOid',
  'dateRange',
  'polarity',
  'tier',
  'taxonomy',
  'hasExternal',
];
/** 任一目標選取條件變更（範圍/階段/篩選欄）→ 重新預覽「將測試 N 筆」。 */
const onTargetChange = () => {
  if (props.scope === 'all' && props.visible) void targets.refreshTargetCount(promptArgs.value);
};
watch(promptArgs, onTargetChange);

const activeTab = ref<'results' | 'history'>('results');
const running = ref(false);
type RunDetail = PromptSandboxRunSummary & {
  results: PromptSandboxItemResult[];
  log: unknown[];
};
const activeRun = ref<RunDetail | null>(null);

/** 評論原文預覽（僅 scope=single 有意義）。 */
const reviewText = computed(() => String(props.row?.content ?? props.row?.title ?? ''));

/** 範圍摘要文字（依 scope 顯示不同語意）。 */
const scopeSummary = computed(() => {
  if (props.scope === 'single') return '單列測試';
  if (props.scope === 'selection') return `已選 ${props.sourceIds.length} 筆`;
  return `依條件批量 · 將測試 ${targets.targetCount.value} 筆`;
});

async function run() {
  if (!selectedCodes.value.length) {
    Message.warning('請至少勾選一支 Prompt');
    return;
  }
  if (props.scope !== 'all' && !props.sourceIds.length) {
    Message.warning('沒有受測項目');
    return;
  }
  running.value = true;
  activeRun.value = null;
  try {
    const body =
      props.scope === 'all'
        ? targets.scopeBody(promptArgs.value)
        : {
            source: props.source,
            item_ids: props.sourceIds,
            prompt_ids: promptArgs.value,
            scope: props.scope,
          };
    const { job_id } = await startPromptSandbox(body);
    // 輪詢至終態（done/error）；沙盒非長批次，短間隔即可即時反映進度。
    while (true) {
      await new Promise((r) => setTimeout(r, 700));
      const snap = await getPromptSandboxStatus(job_id);
      if (snap.status === 'done' && snap.run_id) {
        activeRun.value = await getPromptSandboxRun(snap.run_id);
        await loadHistory();
        break;
      }
      if (snap.status === 'error') {
        Message.error('測試任務失敗');
        break;
      }
    }
  } catch (e) {
    Message.error(e instanceof Error ? e.message : '測試失敗');
  } finally {
    running.value = false;
  }
}

// ── 測試歷史（與正式初判歷史完全分離）──
const history = ref<PromptSandboxRunSummary[]>([]);
const historyLoading = ref(false);
async function loadHistory() {
  historyLoading.value = true;
  try {
    const r = await listPromptSandboxRuns();
    history.value = r.items;
  } catch (e) {
    Message.error(e instanceof Error ? e.message : '載入測試歷史失敗');
  } finally {
    historyLoading.value = false;
  }
}
/** 查看某次歷史測試：拉完整詳情（含 results + log 快照）並切到結果分頁。 */
async function viewHistoryRun(runId: string) {
  try {
    activeRun.value = await getPromptSandboxRun(runId);
    activeTab.value = 'results';
  } catch (e) {
    Message.error(e instanceof Error ? e.message : '載入測試紀錄失敗');
  }
}

/** 範圍中文標籤（歷史列表用）。 */
const SCOPE_LABEL: Record<string, string> = { single: '單列', selection: '選取', all: '依條件' };

/** 域條目判準：有 domain_label 欄位＝域 prompt 結果；否則為 polarity 條目。 */
const isDomainEntry = (p: NonNullable<PromptSandboxItemResult['prompts']>[number]): boolean =>
  p.domain_label !== undefined;

// 開啟時重置狀態 + 載入歷史；勾選帶入至少 polarity（免每次手動勾）；scope='all' 時初始化目標選取器。
watch(
  () => props.visible,
  (v) => {
    if (!v) {
      activeRun.value = null;
      return;
    }
    activeTab.value = 'results';
    if (!selectedCodes.value.length) selectedCodes.value = ['prompt_polarity'];
    if (props.scope === 'all') {
      targets.openTargetPicker();
      void targets.refreshTargetCount(promptArgs.value);
    }
    loadHistory();
  },
);
</script>

<template>
  <a-drawer
    :visible="visible"
    title="Prompt 測試（沙盒 · 不受正式歸因閘門限制 · 不落正式判決）"
    :width="scope === 'all' ? 1040 : 820"
    :footer="false"
    :body-style="{ display: 'flex', flexDirection: 'column', overflow: 'hidden' }"
    @cancel="emit('update:visible', false)"
  >
    <div class="mb-3 text-xs text-[var(--color-text-3)]">
      {{ scopeSummary }} · 勾選要測試的 prompt，即使整體判正向也照跑（不受正式閘門限制）
    </div>

    <!-- 評論原文預覽（僅單列有意義） -->
    <div
      v-if="scope === 'single' && reviewText"
      class="mb-3 rounded-lg border bg-[var(--color-fill-1)] p-3"
    >
      <div class="mb-1 text-xs font-medium text-[var(--color-text-3)]">評論原文</div>
      <div class="max-h-24 overflow-auto whitespace-pre-wrap text-sm leading-relaxed">
        {{ reviewText }}
      </div>
    </div>

    <!-- 依條件批量選取（scope='all'，比照初判分類目標選取）-->
    <div v-if="scope === 'all'" class="mb-3 rounded-lg border p-3">
      <div class="mb-2 flex items-center gap-3">
        <span class="text-xs text-[var(--color-text-3)]">範圍</span>
        <a-radio-group
          v-model="targets.targetMode.value"
          type="button"
          size="small"
          @change="onTargetChange"
        >
          <a-radio value="scope">全部資料</a-radio>
          <a-radio value="selected" :disabled="!selectedKeys?.length">已選內</a-radio>
        </a-radio-group>
      </div>
      <div class="mb-2">
        <div class="mb-1 text-xs text-[var(--color-text-3)]">目標判決階段（預設只測未判）</div>
        <a-checkbox-group v-model="targets.targetStages.value" @change="onTargetChange">
          <a-checkbox v-for="(lbl, code) in STAGE_LABELS" :key="code" :value="code">{{
            lbl
          }}</a-checkbox>
        </a-checkbox-group>
      </div>
      <div class="mb-1 text-xs text-[var(--color-text-3)]">目標篩選（已自動帶入列表當前篩選，可重選）</div>
      <AttributionFilterBar
        :model="targets.draftFilters"
        :fields="TARGET_FIELDS"
        :cascade-options="cascadeOptions"
        @change="onTargetChange"
      />
      <div class="mt-1 text-xs text-[var(--color-text-3)]">
        星等 / 日期 / ID / 外部評論 對所有目標生效；傾向 / 信心分層 / 歸因分類 僅對「已判」階段生效。
      </div>
    </div>

    <!-- 勾選 prompt + 執行 -->
    <div class="mb-3 rounded-lg border p-3">
      <a-checkbox-group v-model="selectedCodes" class="mb-2">
        <a-checkbox v-for="o in promptOptions" :key="o.value" :value="o.value">{{
          o.label
        }}</a-checkbox>
      </a-checkbox-group>
      <div class="flex items-center gap-3">
        <span v-if="scope === 'all'" class="text-xs text-[var(--color-text-3)]"
          >將測試 {{ targets.targetCount.value }} 筆</span
        >
        <div class="flex-1" />
        <a-button
          type="primary"
          size="small"
          :loading="running"
          :disabled="scope === 'all' && !targets.targetCount.value"
          @click="run"
          >執行測試</a-button
        >
      </div>
    </div>

    <a-tabs v-model:active-key="activeTab" class="min-h-0 flex-1">
      <a-tab-pane key="results" title="測試結果">
        <div class="h-full overflow-auto">
          <a-spin v-if="running" class="block py-8 text-center" />
          <template v-else-if="activeRun">
            <div class="mb-2 text-xs text-[var(--color-text-3)]">
              {{ fmtDt(activeRun.created_at) }} · model={{ activeRun.model }} ·
              {{ activeRun.item_count }} 筆
            </div>
            <div class="flex flex-col gap-3">
              <div
                v-for="item in activeRun.results"
                :key="item.source_id"
                class="rounded-lg border p-3"
              >
                <div class="mb-2 flex items-center gap-2">
                  <span class="font-mono text-xs text-[var(--color-text-3)]">{{
                    item.source_id
                  }}</span>
                  <a-tag v-if="item.polarity" size="small">{{ item.polarity }}</a-tag>
                </div>
                <a-alert v-if="item.error" type="error" :content="item.error" />
                <div v-else class="flex flex-col gap-2">
                  <div
                    v-for="(p, i) in item.prompts"
                    :key="i"
                    class="rounded border-l-2 border-[var(--color-border-3)] bg-[var(--color-fill-1)] px-2 py-1.5 text-xs"
                  >
                    <template v-if="isDomainEntry(p)">
                      <div class="flex items-center gap-1.5">
                        <a-tag size="small" :color="p.matched ? 'green' : 'gray'">{{
                          p.matched ? '✅ 命中' : '⭕ 棄權'
                        }}</a-tag>
                        <span class="font-medium">{{ p.domain_label }}</span>
                        <template v-if="p.matched && p.attributions?.[0]">
                          <span class="text-[var(--color-text-3)]">›</span>
                          <span>{{ p.attributions[0].l2_label }}</span>
                          <span class="ml-auto font-mono text-[11px] text-[var(--color-text-3)]"
                            >{{ Math.round((p.attributions[0].confidence ?? 0) * 100) }}%</span
                          >
                        </template>
                      </div>
                      <div class="mt-1 text-[var(--color-text-2)]">
                        {{ p.matched ? p.attributions?.[0]?.reason : p.abstain_reason }}
                      </div>
                    </template>
                    <template v-else>
                      <div class="flex items-center gap-1.5">
                        <a-tag size="small" color="arcoblue">極性</a-tag>
                        <span class="font-medium">{{ p.polarity }}</span>
                        <span class="ml-auto font-mono text-[11px] text-[var(--color-text-3)]"
                          >情緒 {{ p.sentiment_score }}</span
                        >
                      </div>
                      <div v-if="p.reason" class="mt-1 text-[var(--color-text-2)]">
                        {{ p.reason }}
                      </div>
                    </template>
                  </div>
                </div>
              </div>
            </div>
          </template>
          <div v-else class="py-8 text-center text-xs text-[var(--color-text-3)]">
            勾選 Prompt 後點「執行測試」
          </div>
        </div>
      </a-tab-pane>
      <a-tab-pane key="history" title="測試歷史">
        <div class="h-full overflow-auto">
          <a-spin v-if="historyLoading" class="block py-4 text-center" />
          <a-table
            v-else-if="history.length"
            :data="history"
            size="mini"
            :pagination="false"
            row-key="run_id"
          >
            <template #columns>
              <a-table-column title="時間" data-index="created_at" :width="150">
                <template #cell="{ record }">{{ fmtDt(record.created_at) }}</template>
              </a-table-column>
              <a-table-column title="範圍" data-index="scope" :width="70">
                <template #cell="{ record }">{{ SCOPE_LABEL[record.scope] ?? record.scope }}</template>
              </a-table-column>
              <a-table-column title="筆數" data-index="item_count" :width="60" />
              <a-table-column title="Prompt" :width="160" ellipsis tooltip>
                <template #cell="{ record }">{{ record.prompt_ids.join('、') }}</template>
              </a-table-column>
              <a-table-column title="觸發人" data-index="triggered_by" ellipsis tooltip />
              <a-table-column title="" :width="70">
                <template #cell="{ record }">
                  <a-button type="text" size="mini" @click="viewHistoryRun(record.run_id)"
                    >查看</a-button
                  >
                </template>
              </a-table-column>
            </template>
          </a-table>
          <a-empty v-else description="尚無測試紀錄" :image-size="32" />
        </div>
      </a-tab-pane>
    </a-tabs>
  </a-drawer>
</template>

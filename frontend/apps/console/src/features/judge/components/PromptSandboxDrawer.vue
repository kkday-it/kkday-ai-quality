<script setup lang="ts">
/**
 * 歸因列表「Prompt 測試」沙盒抽屜：對單列或勾選多筆，跑使用者勾選的 7 條 prompt 子集
 * （polarity + C-1..C-6）→ 逐筆逐 prompt 結果。**ungated**（不受正式歸因閘門限制，即使整體判
 * 正向也能測域 prompt）；測試歷史與正式初判完全分離（獨立 `prompt_sandbox_runs` 表），且捕捉
 * 完整 LLM log 供歷史回看（見 `sandbox_classify`/`prompt_sandbox.py`）。實時 log 串流為 Phase 2
 * （此版本先留歷史回看，不做即時分頁）。
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

const props = defineProps<{
  /** 是否顯示。 */
  visible: boolean;
  /** 當前反饋來源 code（product_reviews…）。 */
  source: string;
  /** 觸發入口：single＝單列「Prompt 測試」按鈕；selection＝工具列對勾選多筆。 */
  scope: 'single' | 'selection';
  /** 受測 source_id 清單（single 時長度 1）。 */
  sourceIds: string[];
  /** scope=single 時的目標列（供顯示評論原文預覽；selection 無單一列可顯示）。 */
  row?: ProblemRow | null;
}>();

const emit = defineEmits<{ (e: 'update:visible', v: boolean): void }>();

// 從 judgeRules store 取 7 支 prompt 的 rule_code + 中文名（SSOT，同 PromptEvalDrawer 慣例）。
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

const activeTab = ref<'results' | 'history'>('results');
const running = ref(false);
type RunDetail = PromptSandboxRunSummary & {
  results: PromptSandboxItemResult[];
  log: unknown[];
};
const activeRun = ref<RunDetail | null>(null);

/** 評論原文預覽（僅 scope=single 有意義；selection 無單一列可顯示）。 */
const reviewText = computed(() => String(props.row?.content ?? props.row?.title ?? ''));

async function run() {
  if (!selectedCodes.value.length) {
    Message.warning('請至少勾選一支 Prompt');
    return;
  }
  if (!props.sourceIds.length) {
    Message.warning('沒有受測項目');
    return;
  }
  running.value = true;
  activeRun.value = null;
  try {
    const { job_id } = await startPromptSandbox({
      source: props.source,
      source_ids: props.sourceIds,
      prompt_ids: selectedCodes.value.map(toPromptArg),
      scope: props.scope,
    });
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

/** 域條目判準：有 domain_label 欄位＝域 prompt 結果；否則為 polarity 條目。 */
const isDomainEntry = (p: NonNullable<PromptSandboxItemResult['prompts']>[number]): boolean =>
  p.domain_label !== undefined;

// 開啟時重置狀態 + 載入歷史；勾選帶入至少 polarity（免每次手動勾）。
watch(
  () => props.visible,
  (v) => {
    if (!v) {
      activeRun.value = null;
      return;
    }
    activeTab.value = 'results';
    if (!selectedCodes.value.length) selectedCodes.value = ['prompt_polarity'];
    loadHistory();
  },
);
</script>

<template>
  <a-drawer
    :visible="visible"
    title="Prompt 測試（沙盒 · 不受正式歸因閘門限制 · 不落正式判決）"
    :width="820"
    :footer="false"
    :body-style="{ display: 'flex', flexDirection: 'column', overflow: 'hidden' }"
    @cancel="emit('update:visible', false)"
  >
    <div class="mb-3 text-xs text-[var(--color-text-3)]">
      {{ scope === 'single' ? '單列測試' : `已選 ${sourceIds.length} 筆` }} · 勾選要測試的
      prompt，即使整體判正向也照跑（不受正式閘門限制）
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

    <!-- 勾選 prompt + 執行 -->
    <div class="mb-3 rounded-lg border p-3">
      <a-checkbox-group v-model="selectedCodes" class="mb-2">
        <a-checkbox v-for="o in promptOptions" :key="o.value" :value="o.value">{{
          o.label
        }}</a-checkbox>
      </a-checkbox-group>
      <div class="flex items-center gap-3">
        <div class="flex-1" />
        <a-button type="primary" size="small" :loading="running" @click="run">執行測試</a-button>
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
                <template #cell="{ record }">{{
                  record.scope === 'single' ? '單列' : '選取'
                }}</template>
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

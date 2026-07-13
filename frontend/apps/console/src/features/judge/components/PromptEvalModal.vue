<script setup lang="ts">
/**
 * 初判 Prompt 快測彈窗（Prompt-as-Source 調適閉環 UI）：抽 N 則現行判決為參照、只跑當前這支 prompt
 * → 指標卡（域：primary/命中/棄權/多報；極性：polarity/sentiment）+ 逐案分歧表（含診斷理由 reason，
 * B0 overlay：命中取首條歸因理由、棄權取 abstain_reason）。消耗 LLM 額度。
 * 大樣本 / golden / mock / A/B 比較走 CLI scripts/tools/eval_prompt_single.py。
 */
import { computed, ref, watch } from 'vue';
import { Message } from '@arco-design/web-vue';
import { evalPrompt, type PromptEvalResult } from '@/api/judgment.api';

const props = defineProps<{
  /** 是否顯示。 */
  visible: boolean;
  /** 當前 prompt rule_code（prompt_polarity / prompt_C-1~6）。 */
  promptCode: string;
}>();

const emit = defineEmits<{ (e: 'update:visible', v: boolean): void }>();

const n = ref(8);
const loading = ref(false);
const result = ref<PromptEvalResult | null>(null);

/** rule_code → 端點 prompt 參數（prompt_C-3 → C-3、prompt_polarity → polarity）。 */
const promptArg = computed(() => props.promptCode.replace('prompt_', ''));
const isPolarity = computed(() => promptArg.value === 'polarity');

/** 指標卡定義（依 prompt 別）：{label, value(0~1 或 null)}。 */
const metrics = computed(() => {
  const r = result.value;
  if (!r) return [];
  if (isPolarity.value) {
    return [
      { label: 'polarity 一致率', value: r.polarity_match_rate },
      { label: 'sentiment 一致率', value: r.sentiment_match_rate },
    ];
  }
  return [
    { label: 'primary 一致率', value: r.primary_match_rate },
    { label: '命中率', value: r.hit_rate },
    { label: '棄權正確率', value: r.abstain_correct_rate },
    { label: '多報率', value: r.over_report_rate },
  ];
});

/** 0~1 → 百分比字串；null → 「—」（無分母）。 */
const pct = (v?: number | null): string => (v == null ? '—' : `${Math.round(v * 100)}%`);

const mismatchColumns = [
  { title: 'id', dataIndex: 'id', width: 110, ellipsis: true, tooltip: true },
  { title: '參照', slotName: 'ref', width: 150 },
  { title: '本支', slotName: 'pack', width: 150 },
  { title: '理由', dataIndex: 'reason', width: 220, ellipsis: true, tooltip: true },
  { title: '評論', dataIndex: 'text', ellipsis: true, tooltip: true },
];

/** 陣列/值 → 顯示字串（歸因 code 陣列或極性字串）。 */
const fmt = (v: unknown): string =>
  Array.isArray(v) ? v.join('、') || '棄權' : String(v ?? '棄權');

async function run() {
  loading.value = true;
  result.value = null;
  try {
    result.value = await evalPrompt(promptArg.value, n.value);
  } catch (e) {
    Message.error(e instanceof Error ? e.message : '測試失敗');
  } finally {
    loading.value = false;
  }
}

// 關閉時清結果（下次開重測，避免看到上一支殘留）
watch(
  () => props.visible,
  (v) => {
    if (!v) result.value = null;
  },
);
</script>

<template>
  <a-modal
    :visible="visible"
    title="測試此 Prompt（對現行判決）"
    :width="720"
    :footer="false"
    @cancel="emit('update:visible', false)"
  >
    <!-- 配置列：樣本數 + 執行 -->
    <div class="mb-3 flex items-center gap-3">
      <span class="text-xs text-[var(--color-text-3)]">樣本數</span>
      <a-input-number v-model="n" :min="1" :max="30" size="small" class="w-24" />
      <span class="text-[11px] text-[var(--color-text-3)]"
        >抽現行判決 N 則為參照，只跑這支 prompt（消耗 LLM）；大樣本用 CLI</span
      >
      <div class="flex-1" />
      <a-button type="primary" size="small" :loading="loading" @click="run">執行測試</a-button>
    </div>

    <!-- 指標卡 -->
    <div v-if="result" class="mb-3 grid grid-cols-4 gap-2">
      <div
        v-for="m in metrics"
        :key="m.label"
        class="rounded-lg border p-2 text-center"
        :class="{ 'col-span-2': isPolarity }"
      >
        <div class="text-lg font-semibold text-[rgb(var(--primary-6))]">{{ pct(m.value) }}</div>
        <div class="text-[11px] text-[var(--color-text-3)]">{{ m.label }}</div>
      </div>
    </div>
    <div v-if="result" class="mb-2 text-xs text-[var(--color-text-3)]">
      樣本 {{ result.n }} 則 · model={{ result.model }} · 分歧 {{ result.mismatches.length }} 則
    </div>

    <!-- 逐案分歧 -->
    <a-table
      v-if="result && result.mismatches.length"
      :data="result.mismatches"
      :columns="mismatchColumns"
      size="small"
      :pagination="{ pageSize: 8, size: 'mini' }"
      row-key="id"
      :scroll="{ y: 260 }"
    >
      <template #ref="{ record }">
        <span class="font-mono text-xs">{{ fmt(record.ref) }}</span>
      </template>
      <template #pack="{ record }">
        <span class="font-mono text-xs">{{ fmt(record.pack) }}</span>
      </template>
    </a-table>
    <a-empty v-else-if="result" description="無分歧（本支與現行判決一致）" />
    <div v-else class="py-8 text-center text-[var(--color-text-3)]">
      設定樣本數後點「執行測試」
    </div>
  </a-modal>
</template>

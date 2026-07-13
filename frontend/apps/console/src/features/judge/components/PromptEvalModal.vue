<script setup lang="ts">
/**
 * 初判 Prompt 快測彈窗（Prompt-as-Source 調適閉環 UI）：抽 N 則現行判決為參照、只跑當前這支 prompt
 * → 指標卡（域：primary/命中/棄權/多報；極性：polarity/sentiment）+ 逐案分歧表（含診斷理由 reason，
 * B0 overlay：命中取首條歸因理由、棄權取 abstain_reason）。消耗 LLM 額度。
 * 大樣本 / golden / mock / A/B 比較走 CLI scripts/tools/eval_prompt_single.py。
 *
 * 兩種入口：① RuleManager 編某支 prompt 時快測——`promptCode` 固定、`selectable` 關（預設）。
 * ② 歸因列表工具列「測試 Prompt」——`selectable` 開，改用下拉選任一支；`filters` 給定時
 * （B1）樣本＝列表當前篩選子集，非 md5 全表抽樣。
 */
import { computed, onMounted, ref, watch } from 'vue';
import { Message } from '@arco-design/web-vue';
import { evalPrompt, type PrejudgeBody, type PromptEvalResult } from '@/api/judgment.api';
import { useJudgeRulesStore } from '@/stores/judgeRules.store';

const props = withDefaults(
  defineProps<{
    /** 是否顯示。 */
    visible: boolean;
    /** 當前 prompt rule_code（prompt_polarity / prompt_C-1~6）；selectable 時僅作預設選中值。 */
    promptCode?: string;
    /** 開啟後改用下拉選任一支 prompt（歸因列表工具列入口用）。 */
    selectable?: boolean;
    /** B1：給定時樣本＝此篩選子集（PrejudgeBody 同形），取代 md5 全表抽樣。 */
    filters?: PrejudgeBody;
  }>(),
  { promptCode: '', selectable: false, filters: undefined },
);

const emit = defineEmits<{ (e: 'update:visible', v: boolean): void }>();

const n = ref(8);
const loading = ref(false);
const result = ref<PromptEvalResult | null>(null);

// selectable 模式：從 judgeRules store 取 7 支 prompt 的 rule_code + 中文名（SSOT，免另存一份標籤）。
const store = useJudgeRulesStore();
const selectedCode = ref(props.promptCode);
onMounted(async () => {
  if (props.selectable && !store.metas.length) await store.loadList();
  if (!selectedCode.value) {
    selectedCode.value =
      store.metas.find((m) => m.rule_code.startsWith('prompt_'))?.rule_code || 'prompt_polarity';
  }
});
const promptOptions = computed(() =>
  store.metas
    .filter((m) => m.rule_code.startsWith('prompt_'))
    .map((m) => ({ value: m.rule_code, label: store.labelFor(m.rule_code) }))
    .sort((a, b) =>
      a.value === 'prompt_polarity' ? -1 : b.value === 'prompt_polarity' ? 1 : a.value.localeCompare(b.value),
    ),
);

/** rule_code → 端點 prompt 參數（prompt_C-3 → C-3、prompt_polarity → polarity）。 */
const promptArg = computed(() => selectedCode.value.replace('prompt_', ''));
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
    result.value = await evalPrompt(promptArg.value, n.value, props.filters);
  } catch (e) {
    Message.error(e instanceof Error ? e.message : '測試失敗');
  } finally {
    loading.value = false;
  }
}

// 開啟時重置：非 selectable 時每次固定跟隨 promptCode；關閉時清結果（下次開重測，避免看到殘留）。
watch(
  () => props.visible,
  (v) => {
    if (v && !props.selectable && props.promptCode) selectedCode.value = props.promptCode;
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
    <!-- 選 prompt（僅 selectable 入口顯示） -->
    <div v-if="selectable" class="mb-3 flex items-center gap-3">
      <span class="text-xs text-[var(--color-text-3)]">測試 Prompt</span>
      <a-select v-model="selectedCode" size="small" class="w-56" :options="promptOptions" />
    </div>

    <!-- 配置列：樣本數 + 執行 -->
    <div class="mb-3 flex items-center gap-3">
      <span class="text-xs text-[var(--color-text-3)]">樣本數</span>
      <a-input-number v-model="n" :min="1" :max="30" size="small" class="w-24" />
      <span class="text-[11px] text-[var(--color-text-3)]">
        <template v-if="filters">樣本＝當前歸因列表篩選子集（消耗 LLM）</template>
        <template v-else>抽現行判決 N 則為參照，只跑這支 prompt（消耗 LLM）；大樣本用 CLI</template>
      </span>
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
      樣本 {{ result.n }} 則{{ result.filtered ? '（篩選子集）' : '' }} · model={{ result.model }} ·
      分歧 {{ result.mismatches.length }} 則
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

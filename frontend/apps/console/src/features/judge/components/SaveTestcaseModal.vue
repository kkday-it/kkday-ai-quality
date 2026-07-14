<script setup lang="ts">
/**
 * 「存為測試 case」共用彈窗（B3：分歧一鍵入集）：PromptEvalModal 批量分歧表 / RowPromptTestModal
 * 單條測試皆帶入 prefill（文字 + 猜測的域/面向/傾向），使用者確認/修正後存入 prompt_testcases，
 * 邊界測試集自然生長，不靠手工造 CSV。域/面向選項復用既有 `getTaxonomyCascade`（分類結構單一真相源，
 * 不另建一份域清單）。
 */
import { computed, ref, watch } from 'vue';
import { Message } from '@arco-design/web-vue';
import { getTaxonomyCascade, createPromptTestcase, type CascadeNode } from '@/api';

export interface TestcasePrefill {
  text: string;
  goldL1?: string;
  goldL2?: string;
  expectedPolarity?: string;
  note?: string;
}

const props = defineProps<{
  visible: boolean;
  prefill: TestcasePrefill | null;
}>();

const emit = defineEmits<{ (e: 'update:visible', v: boolean): void; (e: 'saved'): void }>();

const cascadeOpts = ref<CascadeNode[]>([]);
const loadingOpts = ref(false);
async function ensureOpts() {
  if (cascadeOpts.value.length || loadingOpts.value) return;
  loadingOpts.value = true;
  try {
    cascadeOpts.value = await getTaxonomyCascade();
  } finally {
    loadingOpts.value = false;
  }
}

const domainOptions = computed(() =>
  cascadeOpts.value.map((n) => ({ value: n.value, label: n.label })),
);
const facetOptions = computed(() => {
  const node = cascadeOpts.value.find((n) => n.value === form.value.goldL1);
  return (node?.children ?? []).map((c) => ({ value: c.value, label: c.label }));
});

const POLARITY_OPTIONS = [
  { value: '', label: '不標' },
  { value: 'negative', label: '負向' },
  { value: 'neutral', label: '中立' },
  { value: 'positive', label: '正向' },
];

const form = ref({ text: '', goldL1: '', goldL2: '', expectedPolarity: '', note: '', tags: '' });
const saving = ref(false);

watch(
  () => props.visible,
  async (v) => {
    if (!v) return;
    await ensureOpts();
    form.value = {
      text: props.prefill?.text ?? '',
      goldL1: props.prefill?.goldL1 ?? '',
      goldL2: props.prefill?.goldL2 ?? '',
      expectedPolarity: props.prefill?.expectedPolarity ?? '',
      note: props.prefill?.note ?? '',
      tags: '',
    };
  },
);
// 換域時舊面向若不屬新域，清空避免送出不合法組合。
watch(
  () => form.value.goldL1,
  () => {
    if (!facetOptions.value.some((f) => f.value === form.value.goldL2)) form.value.goldL2 = '';
  },
);

async function submit() {
  if (!form.value.text.trim()) {
    Message.warning('文字不可為空');
    return;
  }
  if (!form.value.goldL1) {
    Message.warning('請選擇所屬域');
    return;
  }
  saving.value = true;
  try {
    await createPromptTestcase({
      text: form.value.text.trim(),
      gold_l1: form.value.goldL1,
      gold_l2: form.value.goldL2 || undefined,
      expected_polarity: form.value.expectedPolarity || undefined,
      note: form.value.note.trim() || undefined,
      tags: form.value.tags
        ? form.value.tags
            .split(',')
            .map((t) => t.trim())
            .filter(Boolean)
        : undefined,
    });
    Message.success('已存入測試集');
    emit('saved');
    emit('update:visible', false);
  } catch (e) {
    Message.error(e instanceof Error ? e.message : '存入失敗');
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <a-modal
    :visible="visible"
    title="存為邊界測試 case"
    :confirm-loading="saving"
    ok-text="存入測試集"
    @cancel="emit('update:visible', false)"
    @ok="submit"
  >
    <div class="flex flex-col gap-3">
      <div>
        <div class="mb-1 text-xs text-[var(--color-text-3)]">文字</div>
        <a-textarea v-model="form.text" :auto-size="{ minRows: 2, maxRows: 4 }" />
      </div>
      <a-row :gutter="[12, 12]" align="center" wrap>
        <a-col :flex="'160px'">
          <div class="mb-1 text-xs text-[var(--color-text-3)]">所屬域（gold_l1）</div>
          <a-select
            v-model="form.goldL1"
            class="w-full"
            size="small"
            :loading="loadingOpts"
            :options="domainOptions"
            placeholder="選域"
          />
        </a-col>
        <a-col :flex="'160px'">
          <div class="mb-1 text-xs text-[var(--color-text-3)]">面向（gold_l2，可留空）</div>
          <a-select
            v-model="form.goldL2"
            class="w-full"
            size="small"
            allow-clear
            :options="facetOptions"
            placeholder="不指定"
          />
        </a-col>
        <a-col :flex="'140px'">
          <div class="mb-1 text-xs text-[var(--color-text-3)]">預期傾向</div>
          <a-select
            v-model="form.expectedPolarity"
            class="w-full"
            size="small"
            :options="POLARITY_OPTIONS"
          />
        </a-col>
      </a-row>
      <div>
        <div class="mb-1 text-xs text-[var(--color-text-3)]">備註（如分歧描述）</div>
        <a-textarea v-model="form.note" :auto-size="{ minRows: 1, maxRows: 3 }" />
      </div>
      <div>
        <div class="mb-1 text-xs text-[var(--color-text-3)]">標籤（逗號分隔，選填）</div>
        <a-input v-model="form.tags" size="small" placeholder="如 分歧,邊界" />
      </div>
    </div>
  </a-modal>
</template>

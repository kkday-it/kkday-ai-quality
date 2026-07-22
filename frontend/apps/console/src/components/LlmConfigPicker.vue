<script setup lang="ts">
import { computed } from 'vue';
import { PROVIDERS } from '@/features/settings/constants';

/** LLM 連線（供應商）選擇器：canonical 共用元件，配合 LlmKnobs 於「設定 › LLM 連線」與各功能區
 * （prejudge/prompt_debug/sandbox）共用同一組選型控件。小集合互斥檔位（3 家供應商）用
 * radio-group 分段按鈕（同語義控件跨頁一致慣例），非下拉。 */
defineProps<{
  modelValue: string;
  /** 各供應商是否已配 token（未傳則不顯示狀態點）。 */
  providerHasToken?: Record<string, boolean>;
}>();
const emit = defineEmits<{
  (e: 'update:modelValue', value: string): void;
}>();

const options = computed(() => PROVIDERS.map((p) => ({ id: p.id, label: p.short_label ?? p.label })));
</script>

<template>
  <a-radio-group
    :model-value="modelValue"
    type="button"
    size="small"
    @update:model-value="(v) => emit('update:modelValue', String(v))"
  >
    <a-radio v-for="p in options" :key="p.id" :value="p.id">
      <span class="inline-flex items-center gap-1.5">
        <span
          v-if="providerHasToken"
          class="inline-block h-1.5 w-1.5 rounded-full"
          :class="providerHasToken[p.id] ? 'bg-[rgb(var(--green-6))]' : 'bg-[rgb(var(--gray-4))]'"
        />
        {{ p.label }}
      </span>
    </a-radio>
  </a-radio-group>
</template>

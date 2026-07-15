<script setup lang="ts">
/**
 * 7 條判決 prompt 的版本選擇（見 usePromptVersionPicker）：正式判決抽屜、Prompt 測試沙盒共用。
 * 所有 Prompt 測試都在歸因列表以此選版本進行，不支援測試未存檔草稿。
 */
import { onMounted, watch } from 'vue';
import { useJudgeRulesStore } from '@/stores/judgeRules.store';
import {
  usePromptVersionPicker,
  type ResolvedPromptSelection,
} from '../composables/usePromptVersionPicker';

const props = defineProps<{
  /** true 時每支 prompt 前面多一個開關，控制要不要納入本次測試（Prompt 測試沙盒用）。
   * 正式判決固定全 7 支恆納入，不傳或傳 false。 */
  withToggle?: boolean;
}>();
const emit = defineEmits<{
  (e: 'update:resolved', value: ResolvedPromptSelection): void;
  (e: 'update:enabledCodes', value: string[]): void;
}>();

const store = useJudgeRulesStore();
const { promptCodes, selected, enabled, enabledCodes, optionsFor, resolved, ensureLoaded } =
  usePromptVersionPicker({ withToggle: props.withToggle });
onMounted(ensureLoaded);
watch(resolved, (v) => emit('update:resolved', v), { immediate: true, deep: true });
watch(enabledCodes, (v) => emit('update:enabledCodes', v), { immediate: true });
</script>

<template>
  <div class="space-y-1.5">
    <div v-for="code in promptCodes" :key="code" class="flex items-center gap-2">
      <a-switch v-if="withToggle" v-model="enabled[code]" size="small" />
      <span class="w-28 shrink-0 truncate text-xs text-gray-500" :title="store.labelFor(code)">{{
        store.labelFor(code)
      }}</span>
      <a-select
        v-model="selected[code]"
        size="small"
        style="flex: 1"
        :disabled="withToggle && !enabled[code]"
        :options="optionsFor(code)"
      />
    </div>
  </div>
</template>

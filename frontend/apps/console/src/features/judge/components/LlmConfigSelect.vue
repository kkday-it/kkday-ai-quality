<script setup lang="ts">
/**
 * 共用 LLM 模型選擇（本次執行用）：純呈現元件，供正式判決抽屜（批量／單列）與 Prompt 測試抽屜
 * 共用，取代原本各自重複的 a-select 區塊。value 的「預設跟隨全域啟用中」行為由呼叫端自己的
 * `useLlmConfigs()` 實例負責（本元件只負責畫）。
 */
import { computed } from 'vue';
import type { LlmConfigOpt } from '../composables/useLlmConfigs';
import { composeLlmLabel } from '@/features/settings/utils/label.util';

const props = defineProps<{ configs: LlmConfigOpt[] }>();
const llmConfigId = defineModel<string>({ default: '' });
const options = computed(() => props.configs.map((c) => ({ value: c.id, label: composeLlmLabel(c) })));
</script>

<template>
  <div>
    <div class="mb-1 text-xs text-gray-500">LLM 模型配置（同「設定 › LLM 模型連線」）</div>
    <a-select
      v-model="llmConfigId"
      style="width: 100%"
      placeholder="選擇模型（預設啟用中）"
      :options="options"
    />
  </div>
</template>

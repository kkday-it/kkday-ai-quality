<script setup lang="ts">
/**
 * 7 條初判 prompt 的版本選擇（見 usePromptVersionPicker）：每支 prompt 一個下拉，
 * 選項＝該 rule_code 的全版本歷史，預設選中 active 版。消費端＝確認初判分類抽屜（指定歷史版本重判）。
 *
 * **label 用 prompt 檔名 + 中文名**（`01_C-1_content 商品內容`），與 `prompts/` 目錄的實際檔案
 * 一一對得上——這裡是「我要用哪一支 prompt 的哪一版」的場景，只寫中文名的話使用者無法確認自己
 * 選的是不是規則配置裡編輯的那一支。純選單場景仍用 `labelFor`（那裡有 code 徽章當前綴）。
 */
import { onMounted, watch } from 'vue';
import { useJudgeRulesStore } from '@/stores/judgeRules.store';
import {
  usePromptVersionPicker,
  type ResolvedPromptSelection,
} from '../composables/usePromptVersionPicker';

const emit = defineEmits<{
  (e: 'update:resolved', value: ResolvedPromptSelection): void;
}>();

const store = useJudgeRulesStore();
const { promptCodes, selected, optionsFor, resolved, ensureLoaded, reloadHistory, activeVersionOf } =
  usePromptVersionPicker();
onMounted(ensureLoaded);
watch(resolved, (v) => emit('update:resolved', v), { immediate: true, deep: true });

/** 發版後由父層呼叫 reloadHistory（新版本進下拉並選中）。 */
defineExpose({ reloadHistory, activeVersionOf, selected });
</script>

<template>
  <div class="space-y-1.5">
    <div v-for="code in promptCodes" :key="code" class="flex items-center gap-2">
      <span
        class="w-44 shrink-0 truncate text-xs text-gray-500"
        :title="store.qualifiedLabelFor(code)"
      >
        {{ store.qualifiedLabelFor(code) }}
      </span>
      <a-select v-model="selected[code]" size="small" style="flex: 1" :options="optionsFor(code)" />
    </div>
  </div>
</template>

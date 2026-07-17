<script setup lang="ts">
/**
 * 7 條判決 prompt 的版本選擇（見 usePromptVersionPicker）：正式判決抽屜、Prompt 測試沙盒共用。
 * 沙盒（withDrafts）另支援草稿模式：有 DB 草稿的 prompt 下拉多一個「📝 草稿」選項（選中＝以
 * 草稿內容送測、與基準雙跑對比），每列的編輯鈕開草稿編輯抽屜（無草稿時以當前選定版本為底建新草稿）。
 */
import { onMounted, watch } from 'vue';
import { IconEdit } from '@arco-design/web-vue/es/icon';
import { useJudgeRulesStore } from '@/stores/judgeRules.store';
import {
  DRAFT_VERSION,
  usePromptVersionPicker,
  type ResolvedPromptSelection,
} from '../composables/usePromptVersionPicker';

const props = defineProps<{
  /** true 時每支 prompt 前面多一個開關，控制要不要納入本次測試（Prompt 測試沙盒用）。
   * 正式判決固定全 7 支恆納入，不傳或傳 false。 */
  withToggle?: boolean;
  /** true 時啟用草稿模式（沙盒用）：載入草稿存在狀態 + 下拉「草稿」選項 + 每列編輯鈕。 */
  withDrafts?: boolean;
}>();
const emit = defineEmits<{
  (e: 'update:resolved', value: ResolvedPromptSelection): void;
  (e: 'update:enabledCodes', value: string[]): void;
  (e: 'update:draftCodes', value: string[]): void;
  /** 點某列編輯鈕 → 開草稿編輯抽屜；baseVersion＝無草稿時新草稿的分叉基準（當前選定版本或 active）。 */
  (e: 'edit-draft', payload: { code: string; baseVersion: number }): void;
}>();

const store = useJudgeRulesStore();
const {
  promptCodes,
  selected,
  enabled,
  enabledCodes,
  draftMetas,
  draftCodes,
  optionsFor,
  resolved,
  ensureLoaded,
  refreshDrafts,
  reloadHistory,
  activeVersionOf,
} = usePromptVersionPicker({ withToggle: props.withToggle, withDrafts: props.withDrafts });
onMounted(ensureLoaded);
watch(resolved, (v) => emit('update:resolved', v), { immediate: true, deep: true });
watch(enabledCodes, (v) => emit('update:enabledCodes', v), { immediate: true });
watch(draftCodes, (v) => emit('update:draftCodes', v), { immediate: true });

/** 開草稿編輯：分叉基準＝當前選定的真實版本（選中草稿哨兵時退回草稿自身的 base_version / active）。 */
function onEditDraft(code: string): void {
  const sel = selected.value[code];
  const base =
    sel != null && sel !== DRAFT_VERSION
      ? sel
      : (draftMetas.value[code]?.base_version ?? activeVersionOf(code) ?? 0);
  emit('edit-draft', { code, baseVersion: base });
}

/** 草稿存檔/刪除後由父層呼叫 refreshDrafts；草稿入庫後呼叫 reloadHistory（新版本進下拉並選中）。 */
defineExpose({ refreshDrafts, reloadHistory, activeVersionOf, selected, DRAFT_VERSION });
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
      <template v-if="withDrafts">
        <a-tooltip :content="draftMetas[code] ? '編輯草稿' : '以當前選定版本為底建立草稿'">
          <a-button size="mini" type="text" @click="onEditDraft(code)">
            <template #icon><IconEdit /></template>
          </a-button>
        </a-tooltip>
      </template>
    </div>
  </div>
</template>

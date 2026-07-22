<script setup lang="ts">
import { computed, ref, watch } from 'vue';

/**
 * 設定面板共用「手風琴卡片」容器：統一 a-collapse 的卡片化外觀與單開（accordion）行為。
 *
 * 「配置」（連線層）與「規則」（初判邏輯層）兩 tab 共用此殼；新增折疊面板時，
 * 消費端只需在預設 slot 內追加一個 `<a-collapse-item>`，外觀與互動自動一致。
 *
 * 兩種使用模式：
 * - 非受控：只傳 `:default-active`，內部自管當前展開面板（規則 tab 等靜態清單適用）。
 * - 受控：用 `v-model:active`，由父層控制當前展開面板（如點「編輯」時需主動展開該面板）。
 */
const props = withDefaults(defineProps<{ defaultActive?: string; active?: string }>(), {
  defaultActive: '',
  active: undefined,
});
const emit = defineEmits<{ (e: 'update:active', key: string): void }>();

// 受控（父層傳 active）優先；未傳時退回內部狀態（初值為 defaultActive）。
// 對外以單值表達「當前展開面板」，對內轉成 Arco accordion 需要的陣列型別，免消費端處理陣列。
const internal = ref<string>(props.active ?? props.defaultActive);
watch(
  () => props.active,
  (v) => {
    if (v !== undefined) internal.value = v;
  },
);
const activeKeys = computed<string[]>({
  get: () => (internal.value ? [internal.value] : []),
  set: (arr) => {
    const key = arr[0] ?? '';
    internal.value = key;
    emit('update:active', key);
  },
});
</script>

<template>
  <a-collapse v-model:active-key="activeKeys" accordion :bordered="false" class="accordion-group">
    <slot />
  </a-collapse>
</template>

<style scoped>
/* Arco collapse 的 header/content 為第三方深層 DOM，且 collapse / collapse-item 皆無
   header-style / body-style prop（僅 a-card 有），故依專案樣式鐵律第 3 順位用 :deep 覆寫，
   把每個面板做成白底卡片。色彩用 arco 語意 token，暗色模式自動對應。 */
.accordion-group :deep(.arco-collapse-item) {
  margin-bottom: 12px;
  border: 1px solid var(--color-neutral-3);
  border-radius: 8px;
  overflow: hidden;
  background-color: var(--color-bg-2);
}

.accordion-group :deep(.arco-collapse-item:last-child) {
  margin-bottom: 0;
}

.accordion-group :deep(.arco-collapse-item-header),
.accordion-group :deep(.arco-collapse-item-content) {
  background-color: var(--color-bg-2);
}
</style>

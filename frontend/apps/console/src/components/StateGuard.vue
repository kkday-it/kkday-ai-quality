<script setup lang="ts">
// 跨頁非同步三態守衛：統一 error / loading / empty / success 四態渲染，
// 消除各頁重複的 a-alert / a-spin / a-empty 與置中寫法不一致問題。
defineOptions({ inheritAttrs: false });

withDefaults(
  defineProps<{
    /** 載入中 */
    loading?: boolean;
    /** 錯誤訊息（非空字串即顯示 error 態，優先級最高） */
    error?: string;
    /** 是否無資料 */
    empty?: boolean;
    /** 空狀態文案 */
    emptyText?: string;
  }>(),
  { loading: false, error: '', empty: false, emptyText: '尚無資料' },
);
</script>

<template>
  <a-alert v-if="error" type="error">{{ error }}</a-alert>
  <div v-else-if="loading" class="py-[60px] text-center"><a-spin /></div>
  <a-empty v-else-if="empty" :description="emptyText" class="py-[60px]" />
  <slot v-else />
</template>

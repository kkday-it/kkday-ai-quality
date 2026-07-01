<script setup lang="ts">
/**
 * 滿高表格佈局：撐滿父容器高度的卡片，內部「表頭（#toolbar）固定 → 中間內容區滾動 → 表尾（#footer）固定」。
 *
 * 收斂各列表頁重複且不一致的 flex 樣板（AttributionList / DataUpload 的 scroll:'100%'、Analytics 的
 * calc max-h）：把「CardSection + body 撐高成 flex 直欄」封裝於此，呼叫端只需於預設插槽放
 * `<a-table class="min-h-0 flex-1" :scroll="{ y: '100%' }" />`（a-table 自帶 sticky 表頭 + 底部分頁），
 * 免每頁手抄 body-style / min-h-0 / flex-1 咒語。
 *
 * 前置：需父鏈提供確定高度——頁面根元素加 `h-full`（AppShell 內容區已是 flex-1 撐高）。
 *
 * @slot extra - 透傳 CardSection #extra：卡片標題右上角（放少量篩選/操作；不提供則顯示 hint）
 * @slot toolbar - 卡片內、內容區上方固定的表頭（通常放較寬的篩選列）
 * @slot default - 中間可滾內容區（放 a-table 或任意 overflow 內容；a-table 用 min-h-0 flex-1 + scroll y:'100%'）
 * @slot footer - 固定於底部的表尾（放自訂分頁/操作列；a-table 內建分頁時免用）
 */
import CardSection from './CardSection.vue';

defineProps<{
  /** 卡片標題（透傳 CardSection） */
  title?: string;
  /** 右上灰字說明（透傳 CardSection） */
  hint?: string;
  /** 完整說明（多行 ⓘ popover；透傳 CardSection） */
  desc?: string;
}>();

// body 撐高為 flex 直欄的固定樣式（呼叫端不再手抄；對齊 Analytics 既有做法）
const FILL_BODY = {
  flex: '1',
  minHeight: '0',
  display: 'flex',
  flexDirection: 'column',
} as const;
</script>

<template>
  <CardSection
    :title="title"
    :hint="hint"
    :desc="desc"
    class="flex min-h-0 flex-1 flex-col"
    :body-style="FILL_BODY"
  >
    <template v-if="$slots.extra" #extra><slot name="extra" /></template>
    <div v-if="$slots.toolbar" class="mb-2 shrink-0"><slot name="toolbar" /></div>
    <slot />
    <div v-if="$slots.footer" class="mt-2 shrink-0"><slot name="footer" /></div>
  </CardSection>
</template>

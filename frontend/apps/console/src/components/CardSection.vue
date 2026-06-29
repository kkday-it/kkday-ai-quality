<script setup lang="ts">
// 區塊卡片殼：a-card + 標題 + 右上說明（hint）的統一封裝。
// 其餘 a-card 屬性（hoverable / header-style / class…）透過 attribute fallthrough 自動透傳至根 a-card。
defineProps<{
  /** 卡片標題（亦可用 #title slot 覆蓋） */
  title?: string;
  /** 右上角灰字說明（亦可用 #extra slot 覆蓋為自訂內容） */
  hint?: string;
}>();
</script>

<template>
  <a-card :title="title">
    <template v-if="$slots.title" #title><slot name="title" /></template>
    <template v-if="$slots.extra || hint" #extra>
      <slot name="extra"
        ><span class="text-xs text-[#86909c]">{{ hint }}</span></slot
      >
    </template>
    <slot />
  </a-card>
</template>

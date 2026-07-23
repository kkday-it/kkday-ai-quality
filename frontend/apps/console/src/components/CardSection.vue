<script setup lang="ts">
// 區塊卡片殼：a-card + 標題 + 右上說明（hint）的統一封裝。
// 其餘 a-card 屬性（hoverable / header-style / class…）透過 attribute fallthrough 自動透傳至根 a-card。
// desc 存在時，右上額外渲染 ⓘ 圖示，hover / 點擊以 popover 展示完整說明（多行）。
import { IconInfoCircle } from '@arco-design/web-vue/es/icon';

defineProps<{
  /** 卡片標題（亦可用 #title slot 覆蓋） */
  title?: string;
  /** 右上角灰字說明（一句話；亦可用 #extra slot 覆蓋為自訂內容） */
  hint?: string;
  /** 完整說明（多行）；存在時右上顯示 ⓘ，hover / 點擊以 popover 展開 */
  desc?: string;
}>();
</script>

<template>
  <a-card :title="title">
    <template v-if="$slots.title" #title><slot name="title" /></template>
    <template v-if="$slots.extra || hint || desc" #extra>
      <div class="flex items-center gap-1.5">
        <slot name="extra"
          ><span v-if="hint" class="text-xs text-[var(--color-text-3)]">{{ hint }}</span></slot
        >
        <a-popover v-if="desc" :trigger="['hover', 'click']" position="br">
          <icon-info-circle
            class="cursor-pointer text-[var(--color-text-3)] transition-colors hover:text-[rgb(var(--primary-6))]"
          />
          <template #title>{{ title }}</template>
          <template #content>
            <div class="max-w-xs whitespace-pre-line text-xs leading-relaxed text-gray-600">
              {{ desc }}
            </div>
          </template>
        </a-popover>
      </div>
    </template>
    <slot />
  </a-card>
</template>

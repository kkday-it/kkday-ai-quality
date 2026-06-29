<script setup lang="ts">
import { ref, shallowRef, onMounted, onBeforeUnmount } from 'vue';
import { useResizeObserver } from '@vueuse/core';
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import '@xterm/xterm/css/xterm.css';

/**
 * 共用終端輸出元件（xterm.js 封裝）。
 *
 * 唯讀輸出取向：對外暴露命令式 API（write / writeln / clear / fit），呼叫端透過 ref 驅動——
 * 終端本質為串流，故不以 props 宣告式渲染。容器尺寸變動時自動 refit（VueUse useResizeObserver）。
 */
const props = withDefaults(
  defineProps<{
    /** 終端高度（任意 CSS 長度）；內容超出可垂直滾動。 */
    height?: string;
    /** 字級（px）。 */
    fontSize?: number;
  }>(),
  { height: '11rem', fontSize: 12 },
);

const elRef = ref<HTMLDivElement>();
// xterm 實例為非響應式重物件，shallowRef 避免深層 proxy 化（官方建議勿包進 reactive）。
const term = shallowRef<Terminal>();
const fit = shallowRef<FitAddon>();

onMounted(() => {
  if (!elRef.value) return;
  const t = new Terminal({
    fontSize: props.fontSize,
    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
    convertEol: true, // \n 自動轉 \r\n（多行 error 字串不跑版）
    disableStdin: true, // 唯讀輸出，不接受鍵盤輸入
    cursorBlink: false,
    scrollback: 1000,
    theme: { background: '#0d1117', foreground: '#c9d1d9', cursor: '#0d1117' },
  });
  const f = new FitAddon();
  t.loadAddon(f);
  t.open(elRef.value);
  f.fit();
  term.value = t;
  fit.value = f;
});

// 容器尺寸變動 → 重新 fit（自動於 unmount 清理）
useResizeObserver(elRef, () => fit.value?.fit());

onBeforeUnmount(() => {
  term.value?.dispose();
  term.value = undefined;
  fit.value = undefined;
});

defineExpose({
  /** 寫入原始資料（不附行尾）。支援 ANSI escape（如 `\x1b[32m...\x1b[0m`）。 */
  write: (data: string): void => term.value?.write(data),
  /** 寫入一行（自動附 `\r\n`）。 */
  writeln: (data: string): void => term.value?.writeln(data),
  /** 清空畫面。 */
  clear: (): void => term.value?.clear(),
  /** 手動觸發重新 fit（容器外部尺寸驟變時用）。 */
  fit: (): void => fit.value?.fit(),
});
</script>

<template>
  <!-- 外層負責邊框 / 圓角 / 內距 / 高度；內層為 xterm 掛載點（無內距，避免 fit 量測偏差） -->
  <div class="overflow-hidden rounded-lg border border-[#30363d] bg-[#0d1117] p-2" :style="{ height }">
    <div ref="elRef" class="h-full w-full" />
  </div>
</template>

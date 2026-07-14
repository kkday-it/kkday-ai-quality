<script setup lang="ts">
import { ref, shallowRef, onMounted, onBeforeUnmount } from 'vue';
import { useResizeObserver } from '@vueuse/core';
// 型別靜態引入（編譯期擦除，不進 bundle）；xterm JS 實作改掛載期動態載入，避免把終端庫綁進
// 主 bundle（呼應 code-splitting：首屏不需要者延遲載入）。CSS 體積小，留靜態 side-effect 引入。
import type { Terminal } from '@xterm/xterm';
import type { FitAddon } from '@xterm/addon-fit';
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

// xterm 為掛載期動態載入，term 就緒前呼叫端的 write/writeln/clear 會撲空——
// 先入佇列（含 clear，維持操作順序），onMounted 完成後依序 flush，呼叫端無須感知就緒時機。
type PendingOp = { op: 'write' | 'writeln' | 'clear'; data?: string };
const pending: PendingOp[] = [];
const flushPending = (t: Terminal) => {
  for (const { op, data } of pending) {
    if (op === 'clear') t.clear();
    else t[op](data ?? '');
  }
  pending.length = 0;
};

// 動態載入 xterm 實作 + CSS + fit addon（並行）；掛載期才拉，縮小首屏 bundle。
onMounted(async () => {
  if (!elRef.value) return;
  const [{ Terminal }, { FitAddon }] = await Promise.all([
    import('@xterm/xterm'),
    import('@xterm/addon-fit'),
  ]);
  // await 期間元件可能已 unmount（elRef 掛載點消失）→ 中止，避免建立孤兒實例。
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
  flushPending(t);
});

// 容器尺寸變動 → 重新 fit（自動於 unmount 清理）
useResizeObserver(elRef, () => fit.value?.fit());

onBeforeUnmount(() => {
  term.value?.dispose();
  term.value = undefined;
  fit.value = undefined;
});

defineExpose({
  /** 寫入原始資料（不附行尾）。支援 ANSI escape（如 `\x1b[32m...\x1b[0m`）。term 未就緒時排隊補寫。 */
  write: (data: string): void => {
    if (term.value) term.value.write(data);
    else pending.push({ op: 'write', data });
  },
  /** 寫入一行（自動附 `\r\n`）。term 未就緒時排隊補寫。 */
  writeln: (data: string): void => {
    if (term.value) term.value.writeln(data);
    else pending.push({ op: 'writeln', data });
  },
  /** 清空畫面。term 未就緒時入佇列以維持與 write 的相對順序。 */
  clear: (): void => {
    if (term.value) term.value.clear();
    else pending.push({ op: 'clear' });
  },
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

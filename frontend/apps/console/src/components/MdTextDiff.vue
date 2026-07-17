<script setup lang="ts">
/**
 * 兩段純文字的行級 diff 並排檢視（jsdiff diffLines）：左＝舊（del 標紅）、右＝新（add 標綠）、
 * 未動行兩側對齊灰字；文字變更時自動捲到第一處差異。純展示元件（零業務耦合）：吃兩段文字與
 * 標籤，不管內容從哪來——版本對比（PromptDiffCompare 選版後委派）與草稿入庫確認共用。
 */
import { computed, nextTick, ref, shallowRef, watch } from 'vue';
import { diffLines } from 'diff';

const props = defineProps<{
  /** 舊文字（左側；del 標紅）。 */
  oldText: string;
  /** 新文字（右側；add 標綠）。 */
  newText: string;
  /** 左欄頭標籤。 */
  oldLabel?: string;
  /** 右欄頭標籤。 */
  newLabel?: string;
}>();

/** 單側儲存格：del/add＝變更行標色，ctx＝未動行，empty＝對側佔位（本側該邏輯列無對應行）。 */
interface DiffCell {
  type: 'del' | 'add' | 'ctx' | 'empty';
  text: string;
}
/** 一邏輯列＝左右各一儲存格（左右等高對齊）；changed 標記本列屬變更（供捲動定位 + 統計）。 */
interface DiffRow {
  left: DiffCell;
  right: DiffCell;
  changed: boolean;
}

const rows = shallowRef<DiffRow[]>([]);
const containerRef = ref<HTMLElement>();

/** md 全文切行（去尾端換行產生的空 token）。 */
const splitLines = (v: string): string[] => v.replace(/\n$/, '').split('\n');

/** jsdiff 行級對比 → 對齊成左右並排列。
 *
 * 對齊策略：ctx 兩側同行；removed 緊接 added（＝一段修改）逐行配對、長短側以 empty 佔位補齊；
 * 落單 removed 只填左、落單 added 只填右——確保同一邏輯改動左右視覺對齊。 */
function buildRows(): void {
  const parts = diffLines(props.oldText, props.newText);
  const out: DiffRow[] = [];
  for (let i = 0; i < parts.length; i++) {
    const p = parts[i];
    if (!p.added && !p.removed) {
      for (const text of splitLines(p.value)) {
        out.push({ left: { type: 'ctx', text }, right: { type: 'ctx', text }, changed: false });
      }
    } else if (p.removed) {
      const dels = splitLines(p.value);
      const next = parts[i + 1];
      if (next?.added) {
        const adds = splitLines(next.value);
        const n = Math.max(dels.length, adds.length);
        for (let k = 0; k < n; k++) {
          out.push({
            left: k < dels.length ? { type: 'del', text: dels[k] } : { type: 'empty', text: '' },
            right: k < adds.length ? { type: 'add', text: adds[k] } : { type: 'empty', text: '' },
            changed: true,
          });
        }
        i++; // 已消化下一段 added
      } else {
        for (const text of dels) {
          out.push({
            left: { type: 'del', text },
            right: { type: 'empty', text: '' },
            changed: true,
          });
        }
      }
    } else {
      // 落單 added（前一段非 removed）
      for (const text of splitLines(p.value)) {
        out.push({
          left: { type: 'empty', text: '' },
          right: { type: 'add', text },
          changed: true,
        });
      }
    }
  }
  rows.value = out;
  void scrollToFirstChange();
}

/** 重算後捲到第一處變更（限本對比容器內，不動頁面捲軸）。 */
async function scrollToFirstChange(): Promise<void> {
  await nextTick();
  const idx = rows.value.findIndex((r) => r.changed);
  const c = containerRef.value;
  if (idx < 0 || !c) return;
  const el = c.querySelector<HTMLElement>(`[data-row="${idx}"]`);
  if (el) c.scrollTop = Math.max(0, el.offsetTop - 48); // 48＝sticky 欄頭高 + 留白
}

watch(() => [props.oldText, props.newText], buildRows, { immediate: true });

const addCount = computed(() => rows.value.filter((r) => r.right.type === 'add').length);
const delCount = computed(() => rows.value.filter((r) => r.left.type === 'del').length);

/** 單側儲存格底色 + 字色（del 紅 / add 綠 / ctx 灰 / empty 佔位淺底）。 */
const cellClass = (cell: DiffCell): string => {
  switch (cell.type) {
    case 'del':
      return 'bg-[rgba(var(--red-2),0.6)] text-[rgb(var(--red-8))]';
    case 'add':
      return 'bg-[rgba(var(--green-2),0.6)] text-[rgb(var(--green-8))]';
    case 'empty':
      return 'bg-[var(--color-fill-2)]';
    default:
      return 'text-[var(--color-text-2)]';
  }
};
</script>

<template>
  <div class="flex min-h-0 flex-col">
    <div class="mb-1 flex items-center gap-2 text-xs">
      <div class="flex-1" />
      <span class="text-[rgb(var(--green-6))]">+{{ addCount }}</span>
      <span class="text-[rgb(var(--red-6))]">-{{ delCount }}</span>
    </div>
    <!-- 左右並排 diff：單一捲動容器 + 每列 flex 兩格等高對齊；relative 供捲動定位 offsetTop 基準 -->
    <div
      ref="containerRef"
      class="relative min-h-0 flex-1 overflow-auto rounded border bg-[var(--color-fill-1)] font-mono text-xs leading-relaxed"
    >
      <!-- 欄頭（sticky）：左舊 / 右新 -->
      <div
        class="sticky top-0 z-10 flex border-b bg-[var(--color-bg-2)] font-sans text-[var(--color-text-3)]"
      >
        <div class="w-1/2 border-r px-2 py-1">{{ oldLabel ?? '舊' }}</div>
        <div class="w-1/2 px-2 py-1">{{ newLabel ?? '新' }}</div>
      </div>
      <div v-if="!rows.some((r) => r.changed)" class="p-3 font-sans text-[var(--color-text-3)]">
        兩側內容相同，無差異
      </div>
      <div v-for="(r, i) in rows" :key="i" :data-row="i" class="flex items-stretch">
        <!-- 左（舊）-->
        <div class="w-1/2 border-r whitespace-pre-wrap break-words px-2" :class="cellClass(r.left)">
          <span class="mr-1 inline-block w-3 select-none opacity-60">{{
            r.left.type === 'del' ? '-' : ' '
          }}</span
          >{{ r.left.text || (r.left.type === 'empty' ? '' : ' ') }}
        </div>
        <!-- 右（新）-->
        <div class="w-1/2 whitespace-pre-wrap break-words px-2" :class="cellClass(r.right)">
          <span class="mr-1 inline-block w-3 select-none opacity-60">{{
            r.right.type === 'add' ? '+' : ' '
          }}</span
          >{{ r.right.text || (r.right.type === 'empty' ? '' : ' ') }}
        </div>
      </div>
    </div>
  </div>
</template>

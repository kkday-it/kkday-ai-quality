<script setup lang="ts">
/**
 * 初判 Prompt 版本對比：選兩版 → 對 content.text（md 全文）做行級 diff（jsdiff diffLines），
 * **左右並排**（左＝舊版標紅刪、右＝新版標綠增、未動行兩側對齊灰字），開啟即自動捲到第一處變更。
 *
 * 與 VersionDiffCompare（JSON 樹 diff）同 props 介面（history/fetch/active），為 prompt_* 的 drop-in：
 * prompt content 非 L1-L2 樹、jsondiffpatch 對整段 md 無意義，故改行級文字 diff。
 */
import { computed, nextTick, ref, shallowRef, watch } from 'vue';
import { diffLines } from 'diff';
import type { RuleVersionMeta } from '@/api/judgeRules.api';
import { versionLabel } from '../utils';

const props = defineProps<{
  /** 版本清單（新→舊；供下拉選項與初始選版）。 */
  history: RuleVersionMeta[];
  /** 依版本號取內容（{_meta, text}）；本元件取 .text 做 diff。 */
  fetch: (version: number) => Promise<Record<string, unknown>>;
  /** 是否啟用（頁內恆 true）。 */
  active?: boolean;
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

const verA = ref<number>(); // 舊（前）
const verB = ref<number>(); // 新（後）
const rows = shallowRef<DiffRow[]>([]);
const loading = ref(false);
const cache = new Map<number, string>();
const containerRef = ref<HTMLElement>();

/** version → 秒級時間戳版本名（下拉標籤 / 欄頭）。 */
const labelOf = (version?: number): string => {
  const h = props.history.find((x) => x.version === version);
  return versionLabel(h?.created_at, version ?? null);
};

const options = computed(() =>
  props.history.map((h) => ({ value: h.version, label: labelOf(h.version) })),
);

/** 取某版 md 全文（快取；content.text 非字串回空）。 */
async function fetchText(version: number): Promise<string> {
  const hit = cache.get(version);
  if (hit !== undefined) return hit;
  const c = await props.fetch(version);
  const t = typeof c.text === 'string' ? c.text : '';
  cache.set(version, t);
  return t;
}

/** md 全文切行（去尾端換行產生的空 token）。 */
const splitLines = (v: string): string[] => v.replace(/\n$/, '').split('\n');

/** 載入兩版 → jsdiff 行級對比 → 對齊成左右並排列。
 *
 * 對齊策略：ctx 兩側同行；removed 緊接 added（＝一段修改）逐行配對、長短側以 empty 佔位補齊；
 * 落單 removed 只填左、落單 added 只填右——確保同一邏輯改動左右視覺對齊。 */
async function loadDiff(): Promise<void> {
  if (verA.value == null || verB.value == null) return;
  loading.value = true;
  try {
    const [a, b] = await Promise.all([fetchText(verA.value), fetchText(verB.value)]);
    const parts = diffLines(a, b);
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
            out.push({ left: { type: 'del', text }, right: { type: 'empty', text: '' }, changed: true });
          }
        }
      } else {
        // 落單 added（前一段非 removed）
        for (const text of splitLines(p.value)) {
          out.push({ left: { type: 'empty', text: '' }, right: { type: 'add', text }, changed: true });
        }
      }
    }
    rows.value = out;
    await scrollToFirstChange();
  } finally {
    loading.value = false;
  }
}

/** 開啟/重載後捲到第一處變更（限本對比容器內，不動頁面捲軸）。 */
async function scrollToFirstChange(): Promise<void> {
  await nextTick();
  const idx = rows.value.findIndex((r) => r.changed);
  const c = containerRef.value;
  if (idx < 0 || !c) return;
  const el = c.querySelector<HTMLElement>(`[data-row="${idx}"]`);
  if (el) c.scrollTop = Math.max(0, el.offsetTop - 48); // 48＝sticky 欄頭高 + 留白
}

/** 初始選版：verB＝最新（active）、verA＝次新（無則同最新）。 */
function initVersions(): void {
  if (!props.history.length) return;
  verB.value = props.history[0]?.version;
  verA.value = (props.history[1] ?? props.history[0])?.version;
}

watch(() => props.history, initVersions, { immediate: true });
watch([verA, verB], loadDiff);
watch(
  () => props.active,
  (on) => {
    if (on) loadDiff();
  },
);

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
  <div class="rounded-lg border p-3">
    <!-- 選版列：舊 → 新 + 增刪統計 -->
    <div class="mb-2 flex items-center gap-2 text-xs">
      <span class="text-[var(--color-text-3)]">對比</span>
      <a-select v-model="verA" size="small" :options="options" class="w-48" placeholder="舊版本" />
      <span class="text-[var(--color-text-3)]">→</span>
      <a-select v-model="verB" size="small" :options="options" class="w-48" placeholder="新版本" />
      <div class="flex-1" />
      <span class="text-[rgb(var(--green-6))]">+{{ addCount }}</span>
      <span class="text-[rgb(var(--red-6))]">-{{ delCount }}</span>
    </div>
    <!-- 左右並排 diff：單一捲動容器 + 每列 flex 兩格等高對齊；relative 供捲動定位 offsetTop 基準 -->
    <a-spin :loading="loading" class="block">
      <div
        ref="containerRef"
        class="relative max-h-[46vh] overflow-auto rounded border bg-[var(--color-fill-1)] font-mono text-xs leading-relaxed"
      >
        <!-- 欄頭（sticky）：左舊 / 右新 -->
        <div
          class="sticky top-0 z-10 flex border-b bg-[var(--color-bg-2)] font-sans text-[var(--color-text-3)]"
        >
          <div class="w-1/2 border-r px-2 py-1">{{ labelOf(verA) }}（舊）</div>
          <div class="w-1/2 px-2 py-1">{{ labelOf(verB) }}（新）</div>
        </div>
        <div v-if="!rows.length" class="p-3 text-[var(--color-text-3)]">
          選兩個版本以檢視 md 差異
        </div>
        <div v-for="(r, i) in rows" v-else :key="i" :data-row="i" class="flex items-stretch">
          <!-- 左（舊）-->
          <div
            class="w-1/2 border-r whitespace-pre-wrap break-words px-2"
            :class="cellClass(r.left)"
          >
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
    </a-spin>
  </div>
</template>

<script setup lang="ts">
/**
 * 初判 Prompt 版本對比：選兩版 → 對 content.text（md 全文）做行級 diff（jsdiff diffLines），
 * git 風格標紅（刪）/標綠（增）/灰（未動）——最適合審 prompt 文字改動。
 *
 * 與 VersionDiffCompare（JSON 樹 diff）同 props 介面（history/fetch/active），為 prompt_* 的 drop-in：
 * prompt content 非 L1-L3 樹、jsondiffpatch 對整段 md 無意義，故改行級文字 diff。
 */
import { computed, ref, shallowRef, watch } from 'vue';
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

/** 單行 diff 結果：類型 + 內容。 */
interface DiffLine {
  type: 'add' | 'del' | 'ctx';
  text: string;
}

const verA = ref<number>(); // 舊（前）
const verB = ref<number>(); // 新（後）
const lines = shallowRef<DiffLine[]>([]);
const loading = ref(false);
const cache = new Map<number, string>();

/** version → 秒級時間戳版本名（下拉標籤）。 */
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

/** 載入兩版 → jsdiff 行級對比 → 攤平成標色行。 */
async function loadDiff(): Promise<void> {
  if (verA.value == null || verB.value == null) return;
  loading.value = true;
  try {
    const [a, b] = await Promise.all([fetchText(verA.value), fetchText(verB.value)]);
    const out: DiffLine[] = [];
    for (const part of diffLines(a, b)) {
      const type: DiffLine['type'] = part.added ? 'add' : part.removed ? 'del' : 'ctx';
      // 尾端換行會產生一個空 token，去除避免多一列空白
      const partLines = part.value.replace(/\n$/, '').split('\n');
      for (const text of partLines) out.push({ type, text });
    }
    lines.value = out;
  } finally {
    loading.value = false;
  }
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

const addCount = computed(() => lines.value.filter((l) => l.type === 'add').length);
const delCount = computed(() => lines.value.filter((l) => l.type === 'del').length);
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
    <!-- 行級 diff（git 風格）：等寬字、增綠刪紅、可捲動 -->
    <a-spin :loading="loading" class="block">
      <div
        class="max-h-[46vh] overflow-auto rounded border bg-[var(--color-fill-1)] font-mono text-xs leading-relaxed"
      >
        <div v-if="!lines.length" class="p-3 text-[var(--color-text-3)]">
          選兩個版本以檢視 md 差異
        </div>
        <div
          v-for="(l, i) in lines"
          v-else
          :key="i"
          class="whitespace-pre-wrap break-words px-2"
          :class="{
            'bg-[rgba(var(--green-2),0.6)] text-[rgb(var(--green-8))]': l.type === 'add',
            'bg-[rgba(var(--red-2),0.6)] text-[rgb(var(--red-8))]': l.type === 'del',
            'text-[var(--color-text-2)]': l.type === 'ctx',
          }"
        >
          <span class="mr-1 inline-block w-3 select-none opacity-60">{{
            l.type === 'add' ? '+' : l.type === 'del' ? '-' : ' '
          }}</span
          >{{ l.text || ' ' }}
        </div>
      </div>
    </a-spin>
  </div>
</template>

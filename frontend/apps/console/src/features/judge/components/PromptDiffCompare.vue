<script setup lang="ts">
/**
 * 初判 Prompt 版本對比：選兩版 → 對 content.text（md 全文）做行級 diff，左右並排渲染
 * （渲染核心委派公共元件 MdTextDiff：左＝舊版標紅刪、右＝新版標綠增、自動捲到第一處變更）。
 *
 * 與 VersionDiffCompare（JSON 樹 diff）同 props 介面（history/fetch/active），為 prompt_* 的 drop-in：
 * prompt content 非 L1-L2 樹、jsondiffpatch 對整段 md 無意義，故改行級文字 diff。
 */
import { computed, ref, watch } from 'vue';
import type { RuleVersionMeta } from '@/api/judgeRules.api';
import { MdTextDiff } from '@/components';
import { versionLabel } from '../utils';

const props = defineProps<{
  /** 版本清單（新→舊；供下拉選項與初始選版）。 */
  history: RuleVersionMeta[];
  /** 依版本號取內容（{_meta, text}）；本元件取 .text 做 diff。 */
  fetch: (version: number) => Promise<Record<string, unknown>>;
  /** 是否啟用（頁內恆 true）。 */
  active?: boolean;
}>();

const verA = ref<number>(); // 舊（前）
const verB = ref<number>(); // 新（後）
const textA = ref('');
const textB = ref('');
const loading = ref(false);
const cache = new Map<number, string>();

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

/** 載入兩版全文 → 交給 MdTextDiff 渲染。 */
async function loadDiff(): Promise<void> {
  if (verA.value == null || verB.value == null) return;
  loading.value = true;
  try {
    [textA.value, textB.value] = await Promise.all([fetchText(verA.value), fetchText(verB.value)]);
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
</script>

<template>
  <div class="rounded-lg border p-3">
    <!-- 選版列：舊 → 新 -->
    <div class="mb-2 flex items-center gap-2 text-xs">
      <span class="text-[var(--color-text-3)]">對比</span>
      <a-select v-model="verA" size="small" :options="options" class="w-48" placeholder="舊版本" />
      <span class="text-[var(--color-text-3)]">→</span>
      <a-select v-model="verB" size="small" :options="options" class="w-48" placeholder="新版本" />
    </div>
    <a-spin :loading="loading" class="block">
      <div v-if="verA == null || verB == null" class="p-3 text-xs text-[var(--color-text-3)]">
        選兩個版本以檢視 md 差異
      </div>
      <MdTextDiff
        v-else
        class="max-h-[46vh]"
        :old-text="textA"
        :new-text="textB"
        :old-label="`${labelOf(verA)}（舊）`"
        :new-label="`${labelOf(verB)}（新）`"
      />
    </a-spin>
  </div>
</template>

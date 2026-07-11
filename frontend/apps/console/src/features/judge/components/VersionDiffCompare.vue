<script setup lang="ts">
/**
 * 版本對比區塊（頁內面板 / 彈窗共用）：選兩版 → 並排唯讀 JSON 檢視，變動處標紅、
 * 展開至變動節點的祖先、雙欄捲動對齊首個變動，方便逐處比對。
 *
 * 抽出動機：RuleHistoryPanel（頁內）與 RuleHistoryModal（彈窗）原各持一份幾乎相同的對比邏輯，
 * 「修 bug / 加 diff」需兩處同步改 → 抽為單一元件消除漂移。版本清單 + 恢復仍留在各消費端。
 */
import { computed, nextTick, ref, shallowRef, watch } from 'vue';
import { Message } from '@arco-design/web-vue';
import JsonEditor from '@/components/JsonEditor.vue';
import type { RuleVersionMeta } from '@/api/judgeRules.api';
import { diffJsonPaths, jsonPathKey, versionLabel } from '../utils';

/** vanilla-jsoneditor 節點路徑（套件未公開匯出 JSONPath 型別，本地別名）。 */
type JsonPath = string[];

const props = defineProps<{
  /** 版本清單（新→舊；供下拉選項與初始選版）。 */
  history: RuleVersionMeta[];
  /** 依版本號取內容（呼叫端注入規則 API；本元件負責快取）。 */
  fetch: (version: number) => Promise<Record<string, unknown>>;
  /** 是否啟用：頁內恆 true；彈窗綁 visible。false→true 時初始化選版並載入。 */
  active?: boolean;
}>();

const verA = ref<number>(); // 舊（前）
const verB = ref<number>(); // 新（後）
const contentA = shallowRef<Record<string, unknown>>({});
const contentB = shallowRef<Record<string, unknown>>({});
const loading = ref(false);
const cache = new Map<number, Record<string, unknown>>();

const editorA = ref<InstanceType<typeof JsonEditor>>();
const editorB = ref<InstanceType<typeof JsonEditor>>();

const diff = shallowRef(diffJsonPaths({}, {}));
/** onClassName：變動節點回傳標紅 class（兩欄共用同一變動集合）。 */
const classFn = computed(
  () =>
    (path: JsonPath): string | undefined =>
      diff.value.changed.has(jsonPathKey(path)) ? 'jse-diff-changed' : undefined,
);

/** version → 秒級時間戳版本名（供並排面板標頭）。 */
const labelOf = (version?: number): string => {
  const h = props.history.find((x) => x.version === version);
  return versionLabel(h?.created_at, version ?? null);
};

async function fetchContent(version: number): Promise<Record<string, unknown>> {
  const hit = cache.get(version);
  if (hit) return hit;
  const c = await props.fetch(version);
  cache.set(version, c);
  return c;
}

/** 載入兩版內容 → 計算 diff → 待 editor 套用後展開祖先並捲動對齊。 */
async function loadPanes(): Promise<void> {
  if (verA.value == null || verB.value == null) return;
  loading.value = true;
  try {
    const [a, b] = await Promise.all([fetchContent(verA.value), fetchContent(verB.value)]);
    contentA.value = a;
    contentB.value = b;
    diff.value = diffJsonPaths(a, b);
    await nextTick(); // 等 JsonEditor 內部 watch 把新內容 + 標紅 updateProps 進 editor
    applyExpandAndScroll();
  } catch (e) {
    Message.error(e instanceof Error ? e.message : '載入版本失敗');
  } finally {
    loading.value = false;
  }
}

/** 兩欄同步：展開所有變動祖先容器（含根），並捲動聚焦首個變動節點以對齊視野。 */
function applyExpandAndScroll(): void {
  const { ancestors, firstPath } = diff.value;
  const expandCb = (rel: JsonPath): boolean => rel.length === 0 || ancestors.has(jsonPathKey(rel));
  for (const ed of [editorA.value, editorB.value]) {
    ed?.expand([], expandCb);
    if (firstPath) void ed?.scrollTo(firstPath);
  }
}

/** 選版：預設「最新 → 次新」。force 時（開啟 / 啟用）強制重設；否則僅在當前選版失效時補選。 */
function pickDefaults(force: boolean): void {
  const versions = props.history.map((h) => h.version);
  if (!versions.length) return;
  if (force || verB.value == null || !versions.includes(verB.value)) {
    verB.value = versions[0]; // 最新
    verA.value = versions[1] ?? versions[0]; // 次新
  }
}

// 啟用（頁內恆 true / 彈窗開啟）→ 強制回到「最新 → 次新」，符合原「每次開啟看最新」行為。
watch(
  () => props.active,
  (act) => {
    if (!act) return;
    pickDefaults(true);
    void loadPanes();
  },
  { immediate: true },
);

// history 於 active 後才非同步載入（loadHistory）→ 補初始化；不覆蓋使用者瀏覽中的有效選擇。
watch(
  () => props.history,
  () => {
    if (!props.active) return;
    pickDefaults(false);
    void loadPanes();
  },
);

watch([verA, verB], loadPanes);
</script>

<template>
  <div>
    <!-- 對比兩版 -->
    <div class="mb-3 flex items-center gap-2">
      <span class="text-xs text-[var(--color-text-3)]">對比</span>
      <a-select v-model="verA" size="small" class="w-44">
        <a-option v-for="h in history" :key="h.version" :value="h.version">
          {{ versionLabel(h.created_at, h.version) }}
        </a-option>
      </a-select>
      <span>→</span>
      <a-select v-model="verB" size="small" class="w-44">
        <a-option v-for="h in history" :key="h.version" :value="h.version">
          {{ versionLabel(h.created_at, h.version) }}
        </a-option>
      </a-select>
      <a-spin v-if="loading" :size="14" />
      <span class="text-xs text-[var(--color-text-3)]">
        <template v-if="diff.changed.size">
          <span class="text-[rgb(var(--danger-6))]">● {{ diff.changed.size }}</span>
          處變動（紅色標記）
        </template>
        <template v-else-if="!loading">兩版內容一致</template>
      </span>
    </div>
    <!-- 並排 JSON 檢視對比（左＝前版 / 右＝後版）；內容於載入後推入，變動處標紅並展開對齊 -->
    <div class="grid grid-cols-2 gap-3">
      <div class="min-w-0">
        <div class="mb-1 font-mono text-xs text-[var(--color-text-3)]">
          {{ labelOf(verA) }}（前）
        </div>
        <JsonEditor ref="editorA" :json="contentA" read-only mode="tree" :on-class-name="classFn" />
      </div>
      <div class="min-w-0">
        <div class="mb-1 font-mono text-xs text-[var(--color-text-3)]">
          {{ labelOf(verB) }}（後）
        </div>
        <JsonEditor ref="editorB" :json="contentB" read-only mode="tree" :on-class-name="classFn" />
      </div>
    </div>
  </div>
</template>

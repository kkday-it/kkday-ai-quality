<script setup lang="ts">
/**
 * 歷史對比恢復（頁內面板版）：版本清單（恢復鈕）+ 選兩版並排 JSON 檢視對比。
 * 由 RuleManager 於「歷史」模式渲染於編輯區（取代原彈窗 RuleHistoryModal）；掛載即載入當前規則歷史。
 */
import { computed, onMounted, ref, shallowRef, watch } from 'vue';
import { Message } from '@arco-design/web-vue';
import JsonEditor from '@/components/JsonEditor.vue';
import { getRuleVersion } from '@/api/judgeRules.api';
import { useJudgeRulesStore } from '@/stores/judgeRules.store';
import { versionLabel } from '../utils';

/** 更新時間顯示：ISO → 「YYYY-MM-DD HH:mm:ss」。 */
const fmtTs = (s?: string | null): string =>
  (s || '').replace('T', ' ').replace(/\..*$/, '').replace('Z', '').slice(0, 19);

const store = useJudgeRulesStore();

const verA = ref<number>(); // 舊（前）
const verB = ref<number>(); // 新（後）
const contentA = shallowRef<Record<string, unknown>>({});
const contentB = shallowRef<Record<string, unknown>>({});
const loading = ref(false);
const contentCache = new Map<number, Record<string, unknown>>();

const versions = computed(() => store.history.map((h) => h.version));
/** version → 秒級時間戳版本名（供並排面板標頭）。 */
const labelOf = (version?: number): string => {
  const h = store.history.find((x) => x.version === version);
  return versionLabel(h?.created_at, version ?? null);
};

onMounted(async () => {
  await store.loadHistory();
  contentCache.clear();
  const vs = versions.value;
  verB.value = vs[0]; // 最新
  verA.value = vs[1] ?? vs[0]; // 次新
  await loadPanes();
});

async function fetchContent(version: number): Promise<Record<string, unknown>> {
  if (contentCache.has(version)) return contentCache.get(version)!;
  const c = (await getRuleVersion(store.activeCode, version)).content;
  contentCache.set(version, c);
  return c;
}

/** 載入兩版內容供並排 JSON 檢視對比。 */
async function loadPanes() {
  if (verA.value == null || verB.value == null) return;
  loading.value = true;
  try {
    const [a, b] = await Promise.all([fetchContent(verA.value), fetchContent(verB.value)]);
    contentA.value = a;
    contentB.value = b;
  } catch (e) {
    Message.error(e instanceof Error ? e.message : '載入版本失敗');
  } finally {
    loading.value = false;
  }
}

watch([verA, verB], loadPanes);

async function restore(version: number) {
  try {
    await store.restore(version);
    const h = store.history.find((x) => x.version === version);
    Message.success(`已恢復 ${versionLabel(h?.created_at, version)}（新增 active 版本）`);
    await store.loadHistory();
  } catch (e) {
    Message.error(e instanceof Error ? e.message : '恢復失敗');
  }
}

const columns = [
  { title: '版本', slotName: 'ver', width: 150 },
  { title: '備註', dataIndex: 'note', ellipsis: true, tooltip: true },
  { title: '更新人', dataIndex: 'author', width: 180, ellipsis: true, tooltip: true },
  { title: '更新時間', slotName: 'ts', width: 160 },
  { title: '操作', slotName: 'op', width: 88 },
];
</script>

<template>
  <div class="h-full overflow-auto rounded-lg border p-3">
    <!-- 對比兩版 -->
    <div class="mb-3 flex items-center gap-2">
      <span class="text-xs text-[var(--color-text-3)]">對比</span>
      <a-select v-model="verA" size="small" class="w-44">
        <a-option v-for="h in store.history" :key="h.version" :value="h.version">
          {{ versionLabel(h.created_at, h.version) }}
        </a-option>
      </a-select>
      <span>→</span>
      <a-select v-model="verB" size="small" class="w-44">
        <a-option v-for="h in store.history" :key="h.version" :value="h.version">
          {{ versionLabel(h.created_at, h.version) }}
        </a-option>
      </a-select>
      <a-spin v-if="loading" :size="14" />
    </div>
    <!-- 並排 JSON 檢視對比（左＝前版 / 右＝後版）-->
    <div class="mb-4 grid grid-cols-2 gap-3">
      <div class="min-w-0">
        <div class="mb-1 font-mono text-xs text-[var(--color-text-3)]">{{ labelOf(verA) }}（前）</div>
        <JsonEditor :key="`a-${verA}`" :json="contentA" read-only mode="tree" />
      </div>
      <div class="min-w-0">
        <div class="mb-1 font-mono text-xs text-[var(--color-text-3)]">{{ labelOf(verB) }}（後）</div>
        <JsonEditor :key="`b-${verB}`" :json="contentB" read-only mode="tree" />
      </div>
    </div>
    <!-- 版本清單 + 恢復 -->
    <a-table :data="store.history" :columns="columns" size="small" :pagination="false" row-key="version">
      <template #ver="{ record }">
        <span class="font-mono text-xs">{{ versionLabel(record.created_at, record.version) }}</span>
      </template>
      <template #ts="{ record }">{{ fmtTs(record.created_at) }}</template>
      <template #op="{ record }">
        <a-tag v-if="record.is_active" color="green" size="small">active</a-tag>
        <a-popconfirm v-else content="恢復此版本？（新增 active 版本）" @ok="restore(record.version)">
          <a-button size="mini">恢復</a-button>
        </a-popconfirm>
      </template>
    </a-table>
  </div>
</template>

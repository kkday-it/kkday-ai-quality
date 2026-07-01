<script setup lang="ts">
/** 歷史對比恢復：版本清單（恢復鈕）+ 選兩版 jsondiffpatch 視覺 diff。 */
import { computed, ref, watch } from 'vue';
import { Message } from '@arco-design/web-vue';
import { create } from 'jsondiffpatch';
import { format } from 'jsondiffpatch/formatters/html';
import 'jsondiffpatch/formatters/styles/html.css';
import { getRuleVersion } from '@/api/judgeRules.api';
import { useJudgeRulesStore } from '@/stores/judgeRules.store';

const visible = defineModel<boolean>('visible', { default: false });
const store = useJudgeRulesStore();

// 樹節點 / 陣列以 code 當 hash，提升 diff 對位（移動辨識）
const dp = create({
  objectHash: (o: unknown) =>
    (o && typeof o === 'object' && 'code' in o ? String((o as { code: unknown }).code) : undefined) as string,
  arrays: { detectMove: true },
});

const verA = ref<number>(); // 舊
const verB = ref<number>(); // 新
const diffHtml = ref('');
const loading = ref(false);
const contentCache = new Map<number, Record<string, unknown>>();

const versions = computed(() => store.history.map((h) => h.version));
/** 彈窗標題：動態帶當前規則顯示名（「規則配置」為 RuleManager tab 新名，非固定字串）。 */
const modalTitle = computed(() => `${store.labelFor(store.activeCode)} — 歷史版本`);

watch(visible, async (v) => {
  if (!v) return;
  await store.loadHistory();
  contentCache.clear();
  const vs = versions.value;
  verB.value = vs[0]; // 最新
  verA.value = vs[1] ?? vs[0]; // 次新
  await renderDiff();
});

async function fetchContent(version: number): Promise<Record<string, unknown>> {
  if (contentCache.has(version)) return contentCache.get(version)!;
  const c = (await getRuleVersion(store.activeCode, version)).content;
  contentCache.set(version, c);
  return c;
}

async function renderDiff() {
  if (verA.value == null || verB.value == null) return;
  loading.value = true;
  try {
    const [a, b] = await Promise.all([fetchContent(verA.value), fetchContent(verB.value)]);
    const delta = dp.diff(a, b);
    diffHtml.value = delta
      ? (format(delta, a) ?? '')
      : '<p class="text-[var(--color-text-3)]">兩版本內容相同</p>';
  } catch (e) {
    Message.error(e instanceof Error ? e.message : 'diff 失敗');
  } finally {
    loading.value = false;
  }
}

watch([verA, verB], renderDiff);

async function restore(version: number) {
  try {
    await store.restore(version);
    Message.success(`已恢復 v${version}（新增 active 版本）`);
    await store.loadHistory();
  } catch (e) {
    Message.error(e instanceof Error ? e.message : '恢復失敗');
  }
}

const columns = [
  { title: '版本', dataIndex: 'version', width: 70 },
  { title: '備註', dataIndex: 'note', ellipsis: true, tooltip: true },
  { title: '作者', dataIndex: 'author', width: 100, ellipsis: true },
  { title: '時間', dataIndex: 'created_at', width: 170, ellipsis: true },
  { title: '操作', slotName: 'op', width: 100 },
];
</script>

<template>
  <a-modal v-model:visible="visible" :title="modalTitle" :width="900" :footer="false">
    <!-- 對比兩版 -->
    <div class="mb-3 flex items-center gap-2">
      <span class="text-xs text-[var(--color-text-3)]">對比</span>
      <a-select v-model="verA" size="small" class="w-28">
        <a-option v-for="v in versions" :key="v" :value="v">v{{ v }}</a-option>
      </a-select>
      <span>→</span>
      <a-select v-model="verB" size="small" class="w-28">
        <a-option v-for="v in versions" :key="v" :value="v">v{{ v }}</a-option>
      </a-select>
      <a-spin v-if="loading" :size="14" />
    </div>
    <div
      class="mb-4 max-h-[40vh] overflow-auto rounded border p-2 text-xs"
      v-html="diffHtml"
    />

    <!-- 版本清單 + 恢復 -->
    <a-table :data="store.history" :columns="columns" size="small" :pagination="false" row-key="version">
      <template #op="{ record }">
        <a-tag v-if="record.is_active" color="green" size="small">active</a-tag>
        <a-popconfirm v-else content="恢復此版本？（新增 active 版本）" @ok="restore(record.version)">
          <a-button size="mini">恢復</a-button>
        </a-popconfirm>
      </template>
    </a-table>
  </a-modal>
</template>

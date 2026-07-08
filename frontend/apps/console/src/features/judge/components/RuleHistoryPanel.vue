<script setup lang="ts">
/**
 * 歷史對比恢復（頁內面板版）：版本清單（恢復鈕）+ 選兩版並排 JSON 檢視對比（含變動標紅 / 展開對齊）。
 * 由 RuleManager 於「歷史」模式渲染於編輯區；掛載即載入當前規則歷史。對比區塊委派共用 VersionDiffCompare。
 */
import { onMounted } from 'vue';
import { Message } from '@arco-design/web-vue';
import { getRuleVersion } from '@/api/judgeRules.api';
import { useJudgeRulesStore } from '@/stores/judgeRules.store';
import { versionLabel } from '../utils';
import VersionDiffCompare from './VersionDiffCompare.vue';

/** 更新時間顯示：ISO → 「YYYY-MM-DD HH:mm:ss」。 */
const fmtTs = (s?: string | null): string =>
  (s || '').replace('T', ' ').replace(/\..*$/, '').replace('Z', '').slice(0, 19);

const store = useJudgeRulesStore();

/** 依版本號取內容（注入 VersionDiffCompare；綁定當前 activeCode）。 */
const fetchVersion = async (version: number): Promise<Record<string, unknown>> =>
  (await getRuleVersion(store.activeCode, version)).content;

onMounted(async () => {
  await store.loadHistory();
});

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
    <!-- 對比兩版（並排 JSON + 變動標紅 + 展開對齊）-->
    <VersionDiffCompare class="mb-4" :history="store.history" :fetch="fetchVersion" active />
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

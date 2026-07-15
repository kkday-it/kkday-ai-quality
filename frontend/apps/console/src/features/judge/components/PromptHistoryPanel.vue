<script setup lang="ts">
/**
 * 初判 Prompt 歷史（頁內面板版）：版本清單（恢復鈕）+ 選兩版 md 行級對比（PromptDiffCompare）。
 * 與 RuleHistoryPanel（JSON 樹 diff）同結構，差異＝對比區改用 md 文字 diff——prompt content 非樹。
 */
import { onMounted } from 'vue';
import { Message } from '@arco-design/web-vue';
import { getRuleVersion } from '@/api/judgeRules.api';
import { useJudgeRulesStore } from '@/stores/judgeRules.store';
import { versionLabel } from '../utils';
import PromptDiffCompare from './PromptDiffCompare.vue';

/** 更新時間顯示：ISO → 「YYYY-MM-DD HH:mm:ss」。 */
const fmtTs = (s?: string | null): string =>
  (s || '').replace('T', ' ').replace(/\..*$/, '').replace('Z', '').slice(0, 19);

const store = useJudgeRulesStore();

/** 依版本號取內容（注入 PromptDiffCompare；綁定當前 activeCode）。 */
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
    <!-- 對比兩版（md 行級 diff）-->
    <PromptDiffCompare class="mb-4" :history="store.history" :fetch="fetchVersion" active />
    <!-- 版本清單 + 恢復 -->
    <a-table
      :data="store.history"
      :columns="columns"
      size="small"
      :pagination="false"
      row-key="version"
    >
      <template #ver="{ record }">
        <span class="font-mono text-xs">{{ versionLabel(record.created_at, record.version) }}</span>
      </template>
      <template #ts="{ record }">{{ fmtTs(record.created_at) }}</template>
      <template #op="{ record }">
        <a-tag v-if="record.is_active" color="green" size="small">active</a-tag>
        <a-popconfirm
          v-else
          content="恢復此版本？（新增 active 版本）"
          @ok="restore(record.version)"
        >
          <a-button size="mini">恢復</a-button>
        </a-popconfirm>
      </template>
    </a-table>
  </div>
</template>

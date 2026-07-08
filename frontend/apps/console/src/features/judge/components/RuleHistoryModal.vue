<script setup lang="ts">
/** 歷史對比恢復（彈窗版）：版本清單（恢復鈕）+ 選兩版並排 JSON 檢視對比（含變動標紅 / 展開對齊）。對比區塊委派共用 VersionDiffCompare。 */
import { computed, watch } from 'vue';
import { Message } from '@arco-design/web-vue';
import { getRuleVersion } from '@/api/judgeRules.api';
import { useJudgeRulesStore } from '@/stores/judgeRules.store';
import { versionLabel } from '../utils';
import VersionDiffCompare from './VersionDiffCompare.vue';

/** 更新時間顯示：ISO → 「YYYY-MM-DD HH:mm:ss」（去 T / 小數秒 / Z），避免原始字串過長被截斷。 */
const fmtTs = (s?: string | null): string =>
  (s || '').replace('T', ' ').replace(/\..*$/, '').replace('Z', '').slice(0, 19);

const visible = defineModel<boolean>('visible', { default: false });
const store = useJudgeRulesStore();

/** 彈窗標題：動態帶當前規則顯示名（「規則配置」為 RuleManager tab 新名，非固定字串）。 */
const modalTitle = computed(() => `${store.labelFor(store.activeCode)} — 歷史版本`);

/** 依版本號取內容（注入 VersionDiffCompare；綁定當前 activeCode）。 */
const fetchVersion = async (version: number): Promise<Record<string, unknown>> =>
  (await getRuleVersion(store.activeCode, version)).content;

// 開啟彈窗時載入歷史；VersionDiffCompare 收到 active + history 後自動初始化選版並對比。
watch(visible, async (v) => {
  if (!v) return;
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

// 版本＝秒級時間戳命名；備註縮窄可 ellipsis，更新人/更新時間給足寬度避免截斷。
const columns = [
  { title: '版本', slotName: 'ver', width: 150 },
  { title: '備註', dataIndex: 'note', ellipsis: true, tooltip: true },
  { title: '更新人', dataIndex: 'author', width: 180, ellipsis: true, tooltip: true },
  { title: '更新時間', slotName: 'ts', width: 160 },
  { title: '操作', slotName: 'op', width: 88 },
];
</script>

<template>
  <a-modal v-model:visible="visible" :title="modalTitle" :width="900" :footer="false">
    <!-- 對比兩版（並排 JSON + 變動標紅 + 展開對齊）-->
    <VersionDiffCompare class="mb-4" :history="store.history" :fetch="fetchVersion" :active="visible" />
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
  </a-modal>
</template>

<script setup lang="ts">
/** 歷史對比恢復（抽屜版·code 驅動）：版本清單（恢復鈕）+ 選兩版並排 JSON 檢視對比（含變動標紅 / 展開對齊）。
 * 對比區塊委派共用 VersionDiffCompare。由 props.code 指定規則（**不綁全域 store**·可用於任一規則），
 * 自管歷史載入與恢復；恢復後 emit `restored` 供呼叫端重載當前內容。 */
import { ref, watch } from 'vue';
import { Message } from '@arco-design/web-vue';
import {
  getRuleHistory,
  getRuleVersion,
  restoreRule,
  type RuleVersionMeta,
} from '@/api/judgeRules.api';
import { versionLabel } from '../utils';
import VersionDiffCompare from './VersionDiffCompare.vue';

const props = defineProps<{ code: string; label: string }>();
const emit = defineEmits<{ restored: [] }>();
const visible = defineModel<boolean>('visible', { default: false });

const history = ref<RuleVersionMeta[]>([]);

/** 更新時間顯示：ISO → 「YYYY-MM-DD HH:mm:ss」（去 T / 小數秒 / Z），避免原始字串過長被截斷。 */
const fmtTs = (s?: string | null): string =>
  (s || '').replace('T', ' ').replace(/\..*$/, '').replace('Z', '').slice(0, 19);

/** 依版本號取內容（注入 VersionDiffCompare；綁定 props.code）。 */
const fetchVersion = async (version: number): Promise<Record<string, unknown>> =>
  (await getRuleVersion(props.code, version)).content;

async function loadHistory() {
  history.value = await getRuleHistory(props.code);
}

// 開啟彈窗時載入歷史；VersionDiffCompare 收到 active + history 後自動初始化選版並對比。
watch(visible, async (v) => {
  if (v) await loadHistory();
});

async function restore(version: number) {
  try {
    await restoreRule(props.code, version);
    const h = history.value.find((x) => x.version === version);
    Message.success(`已恢復 ${versionLabel(h?.created_at, version)}（新增 active 版本）`);
    await loadHistory();
    emit('restored'); // 呼叫端重載當前內容 + 版本號
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
  <a-drawer v-model:visible="visible" :title="`${label} — 歷史版本`" :width="900" :footer="false">
    <!-- 對比兩版（並排 JSON + 變動標紅 + 展開對齊）-->
    <VersionDiffCompare class="mb-4" :history="history" :fetch="fetchVersion" :active="visible" />
    <!-- 版本清單 + 恢復 -->
    <a-table :data="history" :columns="columns" size="small" :pagination="false" row-key="version">
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
  </a-drawer>
</template>

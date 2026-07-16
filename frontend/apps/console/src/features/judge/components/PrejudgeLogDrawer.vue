<script setup lang="ts">
import { ref, watch } from 'vue';
import { IconLoading } from '@arco-design/web-vue/es/icon';
import { getJudgmentRunLog } from '@/api';
import PrejudgeLogView from './PrejudgeLogView.vue';
import type { LogEntry } from './PrejudgeLogView.types';

// 判決歷史回看 LLM 執行日誌快照：GET 落庫快照一次性載入，靜態渲染（無 SSE）。
// 唯一消費者＝ JudgmentHistoryDrawer（判決歷史「查看 LLM 日誌」入口開）。

const props = defineProps<{
  visible: boolean;
  /** 抽屜開啟且非空時讀落庫快照。 */
  jobId: string;
}>();
const emit = defineEmits<{ (e: 'update:visible', v: boolean): void }>();

const entries = ref<LogEntry[]>([]);
const loadingHistory = ref(false);
const streamError = ref('');

const _openHistory = async (jid: string) => {
  loadingHistory.value = true;
  try {
    const r = await getJudgmentRunLog(jid);
    entries.value = r.entries;
  } catch (e: any) {
    streamError.value = e?.message || '此任務無執行日誌快照（可能為大批量任務或啟用日誌前的舊判決）';
  } finally {
    loadingHistory.value = false;
  }
};

const _open = (jid: string) => {
  entries.value = [];
  streamError.value = '';
  void _openHistory(jid);
};

watch(
  () => [props.visible, props.jobId] as const,
  ([v, jid]) => {
    if (v && jid) _open(jid);
  },
  { immediate: true },
);
</script>

<template>
  <a-drawer
    :visible="visible"
    :width="880"
    :footer="false"
    unmount-on-close
    :body-style="{ display: 'flex', flexDirection: 'column', overflow: 'hidden', padding: '12px 16px' }"
    @update:visible="(v: boolean) => emit('update:visible', v)"
  >
    <template #title>
      <span>LLM 執行日誌</span>
      <a-tag size="small" class="ml-2 font-mono">{{ jobId }}</a-tag>
      <a-tag color="gray" size="small" class="ml-1">歷史快照</a-tag>
    </template>

    <a-alert v-if="streamError" type="warning" class="mb-2">{{ streamError }}</a-alert>

    <div v-if="loadingHistory" class="flex items-center gap-2 py-6 text-xs text-[#86909c]">
      <icon-loading /> 載入歷史日誌快照…
    </div>
    <!-- 捲動已下沉至 PrejudgeLogView 內部（StickyTabs 的 .arco-tabs-content 為唯一捲動容器，
         tab 列固定、左側掛錨點導航與其並排）；本層僅需給出 bounded 高度，不再自行 overflow-auto。 -->
    <div v-else class="min-h-0 flex-1 overflow-hidden">
      <PrejudgeLogView :entries="entries" :streaming="false" />
    </div>
  </a-drawer>
</template>

<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue';
import { IconLoading } from '@arco-design/web-vue/es/icon';
import { getJudgmentRunLog, prejudgeLogStreamUrl } from '@/api';
import PrejudgeLogView from './PrejudgeLogView.vue';
import type { LogEntry } from './PrejudgeLogView.types';

// 「LLM 執行日誌」抽屜：兩種模式共用同一份渲染（PrejudgeLogView）——
// live（預設，「初判分類」點擊時開）：SSE 增量接收單次 job 的執行日誌，即時渲染。
// history（判決歷史「查看 LLM 日誌」入口開）：GET 落庫快照一次性載入，靜態渲染（無 SSE）。

const props = defineProps<{
  visible: boolean;
  /** startPrejudge 回傳的 job_id；抽屜開啟且非空時依 mode 建立 SSE 串流或讀落庫快照。 */
  jobId: string;
  /** live＝即時串流（預設）；history＝讀落庫快照（判決歷史回看，僅小批量 job 有收集內容）。 */
  mode?: 'live' | 'history';
}>();
const emit = defineEmits<{ (e: 'update:visible', v: boolean): void }>();

const entries = ref<LogEntry[]>([]);
const streaming = ref(false);
const loadingHistory = ref(false);
const streamError = ref('');
let es: EventSource | null = null;

const _close = () => {
  es?.close();
  es = null;
  streaming.value = false;
};

const _openLive = (jid: string) => {
  streaming.value = true;
  es = new EventSource(prejudgeLogStreamUrl(jid));
  // EventSource 自動重連會從 offset=0 整批重放 → 每次連上先清空，避免條目重複
  es.onopen = () => {
    entries.value = [];
  };
  // 自動捲到底改由 PrejudgeLogView 內部處理（捲動容器已下沉至 tab 內的 .arco-tabs-content）
  es.onmessage = (ev) => {
    entries.value.push(JSON.parse(ev.data));
  };
  es.addEventListener('done', () => _close());
  es.addEventListener('error', (ev) => {
    // 後端明確推送的 error event 帶 data（如「此任務無日誌」）；原生連線錯誤無 data → 交給自動重連
    const data = (ev as MessageEvent).data;
    if (data) {
      try {
        streamError.value = JSON.parse(data).detail || '日誌串流失敗';
      } catch {
        streamError.value = '日誌串流失敗';
      }
      _close();
    }
  });
};

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
  _close();
  entries.value = [];
  streamError.value = '';
  if (props.mode === 'history') void _openHistory(jid);
  else _openLive(jid);
};

watch(
  () => [props.visible, props.jobId] as const,
  ([v, jid]) => {
    if (v && jid) _open(jid);
    else if (!v) _close();
  },
  { immediate: true },
);
onBeforeUnmount(_close);
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
      <template v-if="mode === 'history'">
        <a-tag color="gray" size="small" class="ml-1">歷史快照</a-tag>
      </template>
      <template v-else>
        <a-tag v-if="streaming" color="arcoblue" size="small" class="ml-1">
          <template #icon><icon-loading /></template>
          串流中
        </a-tag>
        <a-tag v-else color="green" size="small" class="ml-1">已結束</a-tag>
      </template>
    </template>

    <a-alert v-if="streamError" type="warning" class="mb-2">{{ streamError }}</a-alert>

    <div v-if="loadingHistory" class="flex items-center gap-2 py-6 text-xs text-[#86909c]">
      <icon-loading /> 載入歷史日誌快照…
    </div>
    <!-- min-h-0 讓子層 PrejudgeLogView 的 flex:1 高度鏈成立；捲動交給其內部 .arco-tabs-content
         （tab 固定 + 內容捲動，見 .claude/rules/frontend-vue.md），此處不可再套 overflow-auto -->
    <div v-else class="min-h-0 flex-1 pr-1">
      <PrejudgeLogView :entries="entries" :streaming="streaming" />
    </div>
  </a-drawer>
</template>

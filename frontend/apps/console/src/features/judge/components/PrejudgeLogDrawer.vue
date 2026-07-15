<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue';
import { IconLoading } from '@arco-design/web-vue/es/icon';
import { prejudgeLogStreamUrl } from '@/api';
import PrejudgeLogView from './PrejudgeLogView.vue';
import type { LogEntry } from './PrejudgeLogView.types';

// 初判執行日誌抽屜：SSE 增量接收單次 job 的執行日誌並即時渲染（流式，逐 event 追加）。
// 逐條渲染委派 PrejudgeLogView（與 Prompt 測試沙盒共用同一份渲染，見該元件檔頂註）；
// 本檔只負責 SSE 連線生命週期 + drawer 外殼。

const props = defineProps<{
  visible: boolean;
  /** startPrejudge 回傳的 job_id；抽屜開啟且非空時建立 SSE 串流。 */
  jobId: string;
}>();
const emit = defineEmits<{ (e: 'update:visible', v: boolean): void }>();

const entries = ref<LogEntry[]>([]);
const streaming = ref(false);
const streamError = ref('');
const scrollRef = ref<HTMLElement>();
let es: EventSource | null = null;

const _close = () => {
  es?.close();
  es = null;
  streaming.value = false;
};

const _scrollToBottom = () => {
  requestAnimationFrame(() => {
    scrollRef.value?.scrollTo({ top: scrollRef.value.scrollHeight });
  });
};

const _open = (jid: string) => {
  _close();
  entries.value = [];
  streamError.value = '';
  streaming.value = true;
  es = new EventSource(prejudgeLogStreamUrl(jid));
  // EventSource 自動重連會從 offset=0 整批重放 → 每次連上先清空，避免條目重複
  es.onopen = () => {
    entries.value = [];
  };
  es.onmessage = (ev) => {
    entries.value.push(JSON.parse(ev.data));
    _scrollToBottom();
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
    :body-style="{ display: 'flex', flexDirection: 'column', padding: '12px 16px' }"
    @update:visible="(v: boolean) => emit('update:visible', v)"
  >
    <template #title>
      <span>初判執行日誌</span>
      <a-tag size="small" class="ml-2 font-mono">{{ jobId }}</a-tag>
      <a-tag v-if="streaming" color="arcoblue" size="small" class="ml-1">
        <template #icon><icon-loading /></template>
        串流中
      </a-tag>
      <a-tag v-else color="green" size="small" class="ml-1">已結束</a-tag>
    </template>

    <a-alert v-if="streamError" type="warning" class="mb-2">{{ streamError }}</a-alert>

    <div ref="scrollRef" class="flex-1 overflow-auto pr-1">
      <PrejudgeLogView :entries="entries" :streaming="streaming" />
    </div>
  </a-drawer>
</template>

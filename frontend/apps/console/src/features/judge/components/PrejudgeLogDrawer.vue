<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, watch } from 'vue';
import { IconLoading } from '@arco-design/web-vue/es/icon';
import { prejudgeLogStreamUrl } from '@/api';

// 初判執行日誌抽屜：SSE 增量接收單次 job 的執行日誌並即時渲染（流式，逐 event 追加）。
// LLM 相關條目（輸入參數 / prompt 全文 / 輸出）以高亮卡片突出；其餘階段（載資料/落庫/錯誤）逐行列出。

/** 後端 run_log.emit 的條目形狀（backend/app/judge/run_log.py）。 */
interface LogEntry {
  ts: number;
  /** stage｜llm_request｜llm_prompt｜llm_response｜llm_note｜error */
  kind: string;
  stage: string;
  message: string;
  data?: Record<string, unknown>;
}

const props = defineProps<{
  visible: boolean;
  /** startPrejudge 回傳的 job_id；抽屜開啟且非空時建立 SSE 串流。 */
  jobId: string;
}>();
const emit = defineEmits<{ (e: 'update:visible', v: boolean): void }>();

const entries = ref<LogEntry[]>([]);
const streaming = ref(false);
const streamError = ref('');
const listRef = ref<HTMLElement>();
let es: EventSource | null = null;

const _close = () => {
  es?.close();
  es = null;
  streaming.value = false;
};

const _scrollToBottom = async () => {
  await nextTick();
  listRef.value?.scrollTo({ top: listRef.value.scrollHeight });
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
    void _scrollToBottom();
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

/** epoch 秒 → HH:mm:ss（本地時區）。 */
const fmtTs = (ts: number) => new Date(ts * 1000).toLocaleTimeString('en-GB', { hour12: false });

/** LLM 請求參數 → 過濾空值後的 [key, value] 顯示對。 */
const paramPairs = (data?: Record<string, unknown>) =>
  Object.entries(data ?? {}).filter(([, v]) => v !== null && v !== undefined && v !== '');

const isLlm = (kind: string) => kind.startsWith('llm_');

/** 一般階段 tag 語義色（job 藍 / item 青 / db 綠 / 判決階段紫）。 */
const STAGE_TAG_COLOR: Record<string, string> = {
  job: 'arcoblue',
  item: 'cyan',
  db: 'green',
  polarity: 'purple',
  attribute: 'purple',
};

const LLM_KIND_LABEL: Record<string, string> = {
  llm_request: 'LLM 請求',
  llm_prompt: 'LLM Prompt',
  llm_response: 'LLM 輸出',
  llm_note: 'LLM 註記',
};
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

    <div ref="listRef" class="flex-1 space-y-2 overflow-auto pr-1">
      <div
        v-if="!entries.length && streaming"
        class="flex items-center gap-2 py-6 text-xs text-[#86909c]"
      >
        <icon-loading /> 等待執行日誌…
      </div>

      <template v-for="(e, i) in entries" :key="i">
        <!-- LLM 條目：高亮卡片突出（輸入參數 / prompt 全文 / 輸出 / 降級註記） -->
        <div
          v-if="isLlm(e.kind)"
          class="rounded-md border-l-4 p-2"
          :class="
            e.kind === 'llm_note'
              ? 'border-[#ff9a2e] bg-[#fff7e8]'
              : 'border-[#4080ff] bg-[#f2f7ff]'
          "
        >
          <div class="mb-1 flex flex-wrap items-center gap-2 text-xs">
            <span class="text-[#86909c]">{{ fmtTs(e.ts) }}</span>
            <a-tag :color="e.kind === 'llm_note' ? 'orange' : 'arcoblue'" size="small">
              {{ LLM_KIND_LABEL[e.kind] || e.kind }}
            </a-tag>
            <a-tag size="small">{{ e.stage }}</a-tag>
            <span class="font-medium">{{ e.message }}</span>
          </div>

          <!-- 輸入參數：key-value 平鋪 -->
          <div v-if="e.kind === 'llm_request'" class="flex flex-wrap gap-x-4 gap-y-1 text-xs">
            <span v-for="[k, v] in paramPairs(e.data)" :key="k">
              <span class="text-[#86909c]">{{ k }}:</span>
              <span class="ml-1 font-mono">{{ typeof v === 'object' ? JSON.stringify(v) : v }}</span>
            </span>
          </div>

          <!-- Prompt 全文：System 預設收合（判準法典很長）、User 預設展開 -->
          <a-collapse
            v-else-if="e.kind === 'llm_prompt'"
            :bordered="false"
            :default-active-key="['user']"
          >
            <a-collapse-item key="system" header="System prompt">
              <pre class="max-h-72 overflow-auto whitespace-pre-wrap break-all text-xs">{{
                e.data?.system
              }}</pre>
            </a-collapse-item>
            <a-collapse-item key="user" header="User prompt">
              <pre class="max-h-72 overflow-auto whitespace-pre-wrap break-all text-xs">{{
                e.data?.user
              }}</pre>
            </a-collapse-item>
          </a-collapse>

          <!-- 輸出：原始回應全文 + 用量摘要 -->
          <template v-else-if="e.kind === 'llm_response'">
            <pre
              class="mb-1 max-h-72 overflow-auto whitespace-pre-wrap break-all rounded bg-white/70 p-1.5 text-xs"
              >{{ e.data?.raw ?? JSON.stringify(e.data?.parsed, null, 2) }}</pre
            >
            <div class="flex gap-3 text-xs text-[#86909c]">
              <span v-if="e.data?.latency_ms">latency: {{ e.data.latency_ms }}ms</span>
              <span v-if="e.data?.total_tokens">tokens: {{ e.data.total_tokens }}</span>
              <span v-if="e.data?.reasoning_tokens">
                reasoning tokens: {{ e.data.reasoning_tokens }}
              </span>
            </div>
          </template>

          <!-- 註記（如 reasoning_effort 自動降級）：附原始錯誤 -->
          <div v-else-if="e.data?.error" class="text-xs text-[#86909c]">{{ e.data.error }}</div>
        </div>

        <!-- 一般階段 / 錯誤：逐行輸出 -->
        <div
          v-else
          class="flex items-start gap-2 text-xs"
          :class="e.kind === 'error' ? 'text-[#f53f3f]' : 'text-[#4e5969]'"
        >
          <span class="shrink-0 text-[#86909c]">{{ fmtTs(e.ts) }}</span>
          <a-tag
            size="small"
            :color="e.kind === 'error' ? 'red' : STAGE_TAG_COLOR[e.stage] || 'gray'"
          >
            {{ e.stage }}
          </a-tag>
          <div class="min-w-0">
            <div>{{ e.message }}</div>
            <div v-if="e.data?.content" class="mt-0.5 break-all text-[#86909c]">
              「{{ e.data.content }}」
            </div>
          </div>
        </div>
      </template>
    </div>
  </a-drawer>
</template>

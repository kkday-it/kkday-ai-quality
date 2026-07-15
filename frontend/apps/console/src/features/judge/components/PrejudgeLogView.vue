<script setup lang="ts">
/**
 * 執行日誌逐條渲染（純展示元件，無 SSE 邏輯）：LLM 相關條目（輸入參數 / prompt 全文 / 輸出）
 * 以高亮卡片突出；其餘階段（載資料/落庫/錯誤）逐行列出。抽自 `PrejudgeLogDrawer.vue`，
 * 供該抽屜（即時 SSE）與 Prompt 測試沙盒（即時 SSE + 歷史回看 log 快照）共用同一份渲染，
 * 避免兩處平行實作 drift。
 */
import { IconLoading } from '@arco-design/web-vue/es/icon';
import type { LogEntry } from './PrejudgeLogView.types';

defineProps<{
  entries: LogEntry[];
  /** 是否仍在串流中（true 且 entries 為空時顯示等待占位）；歷史回看靜態 log 傳 false。 */
  streaming?: boolean;
}>();

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
  <div class="space-y-2">
    <div
      v-if="!entries.length && streaming"
      class="flex items-center gap-2 py-6 text-xs text-[#86909c]"
    >
      <icon-loading /> 等待執行日誌…
    </div>
    <a-empty v-else-if="!entries.length" description="無日誌紀錄" :image-size="32" />

    <template v-for="(e, i) in entries" :key="i">
      <!-- LLM 條目：高亮卡片突出（輸入參數 / prompt 全文 / 輸出 / 降級註記） -->
      <div
        v-if="isLlm(e.kind)"
        class="rounded-md border-l-4 p-2"
        :class="
          e.kind === 'llm_note' ? 'border-[#ff9a2e] bg-[#fff7e8]' : 'border-[#4080ff] bg-[#f2f7ff]'
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
        <a-tag size="small" :color="e.kind === 'error' ? 'red' : STAGE_TAG_COLOR[e.stage] || 'gray'">
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
</template>

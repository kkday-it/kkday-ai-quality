<script setup lang="ts">
/**
 * 一次 LLM 調用的時間軸內容（請求 → Prompt 全文 → 回應，由上至下時間遞增）：自 `PrejudgeLogView`
 * 的「每一次 LLM 調用一個 tab」內容區塊抽出，供該檔各 LLM 調用 tab 復用，避免同一份渲染日後
 * 因散落多處而 drift。純時間軸內容——左側掛錨點導航是跨所有調用 tab 共用的固定側欄，由
 * `PrejudgeLogView` 統一持有（見該檔），不屬於單一調用的內容範圍。
 */
import { IconLoading } from '@arco-design/web-vue/es/icon';
import type { LogEntry } from './PrejudgeLogView.types';
import {
  fmtTs,
  LLM_DOT,
  LLM_KIND_LABEL,
  logEntryId,
  objectParams,
  scalarParams,
  tryParseRaw,
} from '../utils';

/** 回應原文純文字展示：JSON 格式可解析 → 美化縮排；否則如實回原文。 */
const formattedResponse = (e: LogEntry): string => {
  const parsed = tryParseRaw(e.data?.raw) ?? e.data?.parsed;
  return parsed ? JSON.stringify(parsed, null, 2) : String(e.data?.raw ?? '');
};

defineProps<{
  entries: LogEntry[];
  /** 分組鍵（polarity / C-1..C-6），供時間軸節點錨點 id 命名（`PrejudgeLogView` 左側導航 href 目標）。 */
  callKey: string;
  /** done＝已取得回應；running＝串流中；incomplete＝串流已止仍未回（例外/額度中斷）。 */
  status: 'running' | 'done' | 'incomplete';
}>();
</script>

<template>
  <a-timeline class="pl-1 pt-2">
    <a-timeline-item
      v-for="(e, i) in entries"
      :key="i"
      :dot-color="LLM_DOT[e.kind] || '#86909c'"
      :label="fmtTs(e.ts)"
    >
      <div :id="logEntryId(callKey, i)" class="text-xs font-medium text-[#1d2129] scroll-mt-2">
        {{ LLM_KIND_LABEL[e.kind] || e.kind }}
        <span class="ml-1 font-normal text-[#86909c]">{{ e.message }}</span>
      </div>

      <!-- 請求：完整輸入參數（100% 對齊實際送 API 的 kwargs，逐項不漏） -->
      <template v-if="e.kind === 'llm_request'">
        <div class="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs">
          <span v-for="[k, v] in scalarParams(e.data)" :key="k">
            <span class="text-[#86909c]">{{ k }}:</span>
            <span class="ml-1 font-mono">{{ v }}</span>
          </span>
        </div>
        <div v-for="[k, v] in objectParams(e.data)" :key="k" class="mt-1">
          <span class="text-xs text-[#86909c]">{{ k }}:</span>
          <!-- 純文字全展示（非可收合樹狀元件）：送 API 的實際參數要一眼看到全部，不帶互動摺疊語意 -->
          <pre class="mt-0.5 whitespace-pre-wrap break-all rounded bg-[#f7f8fa] p-1.5 text-xs">{{
            JSON.stringify(v, null, 2)
          }}</pre>
        </div>
      </template>

      <!-- Prompt 全文：System / User 皆全展示（一眼看到全部，內容區已有獨立捲動容器，不再逐塊定高截斷） -->
      <a-collapse
        v-else-if="e.kind === 'llm_prompt'"
        :bordered="false"
        :default-active-key="['system', 'user']"
        class="mt-1"
      >
        <a-collapse-item key="system" header="System prompt">
          <pre class="whitespace-pre-wrap break-all text-xs">{{ e.data?.system }}</pre>
        </a-collapse-item>
        <a-collapse-item key="user" header="User prompt">
          <pre class="whitespace-pre-wrap break-all text-xs">{{ e.data?.user }}</pre>
        </a-collapse-item>
      </a-collapse>

      <!-- 輸出：原始回應全文純文字展示（JSON 格式可解析 → 美化縮排；否則如實顯示原文），不套
           可收合樹狀元件——回應內容要一眼看到全部。 + 用量摘要 -->
      <template v-else-if="e.kind === 'llm_response'">
        <div class="mt-1 text-xs text-[#86909c]">回應原文</div>
        <pre class="mt-0.5 whitespace-pre-wrap break-all rounded bg-[#f0f9f0] p-1.5 text-xs">{{
          formattedResponse(e)
        }}</pre>
        <div class="mt-0.5 flex gap-3 text-xs text-[#86909c]">
          <span v-if="e.data?.latency_ms">latency: {{ e.data.latency_ms }}ms</span>
          <span v-if="e.data?.total_tokens">tokens: {{ e.data.total_tokens }}</span>
          <span v-if="e.data?.reasoning_tokens">reasoning: {{ e.data.reasoning_tokens }}</span>
        </div>
      </template>

      <!-- 降級註記 -->
      <div v-else-if="e.kind === 'llm_note'" class="mt-0.5 text-xs text-[#86909c]">
        {{ e.data?.error }}
      </div>
    </a-timeline-item>

    <!-- 尾節點：進行中 / 中斷提示 -->
    <a-timeline-item v-if="status === 'running'" dot-color="#4080ff">
      <div class="flex items-center gap-1 text-xs text-[#86909c]"><icon-loading /> 等待回應…</div>
    </a-timeline-item>
    <a-timeline-item v-else-if="status === 'incomplete'" dot-color="#f53f3f">
      <div class="text-xs text-[#f53f3f]">
        此調用未取得回應（可能例外中斷 / 額度不足，詳見「流程」tab 的錯誤）
      </div>
    </a-timeline-item>
  </a-timeline>
</template>

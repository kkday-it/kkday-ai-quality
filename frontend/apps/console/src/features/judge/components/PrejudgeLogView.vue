<script setup lang="ts">
/**
 * 執行日誌渲染（純展示，無 SSE 邏輯）：每一次 LLM 調用（polarity / C-1..C-6）聚合成獨立 tab，
 * tab 內以「時間軸」由上至下（時間遞增）呈現該調用的生命週期——請求（完整輸入參數，100% 對齊
 * 實際送 API 的 kwargs）→ Prompt 全文 → 回應（原始輸出 + 用量）；非 LLM 的流程階段（載資料 /
 * 落庫 / 錯誤）收在「流程」tab 的時間軸。抽自 `PrejudgeLogDrawer.vue`，供該抽屜（即時 SSE）與
 * Prompt 測試沙盒（即時 SSE + 歷史回看快照）共用同一份渲染，避免 drift。
 *
 * Tab 固定 + 內容捲動：交給公共元件 `StickyTabs`（`@/components`，見
 * `.claude/rules/frontend-vue.md` Tabs 切換展示章節）取代裸 `a-tabs`，本檔不再自行處理
 * `:deep()` CSS。
 */
import { computed, nextTick, ref, watch } from 'vue';
import {
  IconCheckCircleFill,
  IconCloseCircleFill,
  IconLoading,
  IconMinusCircleFill,
} from '@arco-design/web-vue/es/icon';
import { StickyTabs } from '@/components';
// 相對路徑 import（非走 barrel）：本檔自身未進 components barrel，但同資料夾 PrejudgeLogDrawer /
// PromptSandboxDrawer 皆以相對路徑消費本檔，此處延續同一慣例（避免同資料夾迴繞 import）。
import LogJsonBlock from './LogJsonBlock.vue';
import type { LogEntry } from './PrejudgeLogView.types';

const props = defineProps<{
  entries: LogEntry[];
  /** 是否仍在串流中（true 且 entries 為空時顯示等待占位）；歷史回看靜態 log 傳 false。 */
  streaming?: boolean;
}>();

// 串流中新條目到達 → 委由 StickyTabs 捲動當前可見 tab 的內容區到底；
// 歷史回看（streaming=false）不自動捲。
const stickyTabsRef = ref<InstanceType<typeof StickyTabs>>();
watch(
  () => props.entries.length,
  async () => {
    if (!props.streaming) return;
    await nextTick();
    stickyTabsRef.value?.scrollActiveToBottom();
  },
);

/** epoch 秒 → HH:mm:ss（本地時區）。 */
const fmtTs = (ts: number) => new Date(ts * 1000).toLocaleTimeString('en-GB', { hour12: false });

const isLlm = (kind: string) => kind.startsWith('llm_');

/** LLM 回應 raw 為 JSON 格式字串（schema/response_format 要求）時解析出物件供樹狀檢視；
 * 解析失敗（非 JSON 輸出 / 錯誤文字）回 null，模板改走純文字顯示，如實呈現原始內容。 */
const tryParseRaw = (raw: unknown): unknown | null => {
  if (typeof raw !== 'string' || !raw.trim()) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
};

/** 一次 LLM 調用的聚合（依 ts 排序的條目 + 狀態），供單一 tab 的時間軸呈現。 */
interface CallGroup {
  key: string;
  entries: LogEntry[];
  status: 'running' | 'done' | 'incomplete';
  /** done 狀態下該次調用是否有非空 attributions（真的歸因到問題，非「域無關回空」）；
   * 非 attribute 域（如 polarity 無 attributions 欄）或尚未 done → null（不顯示標示）。 */
  hasResult: boolean | null;
}

/** 非 LLM 的流程階段（job/item/db/error）→ 時間軸；LLM 條目另按調用分組。 */
const flowEntries = computed(() => props.entries.filter((e) => !isLlm(e.kind)));

/** 按 label（回退 stage）把 LLM 條目聚合成調用清單，維持首次出現順序（＝送出順序）。 */
const callGroups = computed<CallGroup[]>(() => {
  const map = new Map<string, CallGroup>();
  const order: string[] = [];
  for (const e of props.entries) {
    if (!isLlm(e.kind)) continue;
    const key = e.label || e.stage || '?';
    let g = map.get(key);
    if (!g) {
      g = { key, entries: [], status: 'running', hasResult: null };
      map.set(key, g);
      order.push(key);
    }
    g.entries.push(e); // 到達順序＝時間遞增
  }
  for (const g of map.values()) {
    const resp = g.entries.find((e) => e.kind === 'llm_response');
    // 有回應＝完成；串流中未回＝進行中；串流已止仍未回＝該調用中斷（例外/額度）
    g.status = resp ? 'done' : props.streaming ? 'running' : 'incomplete';
    if (resp) {
      const parsed = (tryParseRaw(resp.data?.raw) ?? resp.data?.parsed) as
        | { attributions?: unknown }
        | undefined;
      // 只有 attribute 域回應才有 attributions 欄；is-array 判斷本身即排除 polarity 等無此欄的階段
      g.hasResult = Array.isArray(parsed?.attributions) ? parsed.attributions.length > 0 : null;
    }
  }
  return order.map((k) => map.get(k)!);
});

const activeTab = ref('__flow__');

/** 請求參數 → 分離純量（平鋪）與物件（JSON 區塊），full kwargs 逐項不漏。 */
const scalarParams = (data?: Record<string, unknown>) =>
  Object.entries(data ?? {}).filter(
    ([, v]) => v !== null && v !== undefined && v !== '' && typeof v !== 'object'
  );
const objectParams = (data?: Record<string, unknown>) =>
  Object.entries(data ?? {}).filter(([, v]) => v !== null && typeof v === 'object');

/** 時間軸節點色（依 kind / stage 語義）。 */
const LLM_DOT: Record<string, string> = {
  llm_request: '#4080ff',
  llm_prompt: '#86909c',
  llm_response: '#00b42a',
  llm_note: '#ff7d00',
  error: '#f53f3f',
};
const STAGE_DOT: Record<string, string> = {
  job: '#4080ff',
  item: '#14c9c9',
  db: '#00b42a',
  polarity: '#722ed1',
  attribute: '#722ed1',
};
const flowDot = (e: LogEntry) =>
  e.kind === 'error' ? '#f53f3f' : STAGE_DOT[e.stage] || '#86909c';

const LLM_KIND_LABEL: Record<string, string> = {
  llm_request: 'LLM 請求',
  llm_prompt: 'Prompt 全文',
  llm_response: 'LLM 輸出',
  llm_note: '降級註記',
};
</script>

<template>
  <div class="h-full">
    <div
      v-if="!entries.length && streaming"
      class="flex items-center gap-2 py-6 text-xs text-[#86909c]"
    >
      <icon-loading /> 等待執行日誌…
    </div>
    <a-empty v-else-if="!entries.length" description="無日誌紀錄" :image-size="32" />

    <StickyTabs
      v-else
      ref="stickyTabsRef"
      v-model:active-key="activeTab"
      type="card-gutter"
      size="small"
      :lazy-load="true"
    >
      <!-- 流程 tab：非 LLM 階段時間軸（由上至下時間遞增） -->
      <a-tab-pane key="__flow__">
        <template #title>
          <span>流程</span>
          <a-tag class="ml-1" size="small" color="gray">{{ flowEntries.length }}</a-tag>
        </template>
        <a-timeline class="pl-1 pt-2">
          <a-timeline-item
            v-for="(e, i) in flowEntries"
            :key="i"
            :dot-color="flowDot(e)"
            :label="fmtTs(e.ts)"
          >
            <div class="text-xs" :class="e.kind === 'error' ? 'text-[#f53f3f]' : 'text-[#4e5969]'">
              <a-tag size="small" :color="e.kind === 'error' ? 'red' : 'gray'">{{ e.stage }}</a-tag>
              <span class="ml-1">{{ e.message }}</span>
              <div v-if="e.data?.content" class="mt-0.5 break-all text-[#86909c]">
                「{{ e.data.content }}」
              </div>
              <div
                v-else-if="e.data && Object.keys(e.data).length"
                class="mt-0.5 flex flex-wrap gap-x-3 text-[#86909c]"
              >
                <span v-for="[k, v] in scalarParams(e.data)" :key="k">
                  {{ k }}: <span class="font-mono">{{ v }}</span>
                </span>
              </div>
            </div>
          </a-timeline-item>
        </a-timeline>
      </a-tab-pane>

      <!-- 每一次 LLM 調用一個 tab：時間軸由上至下（請求 → Prompt → 回應） -->
      <a-tab-pane v-for="g in callGroups" :key="g.key">
        <template #title>
          <icon-loading v-if="g.status === 'running'" class="text-[#4080ff]" />
          <!-- done + hasResult=false：該調用正常完成但無非空 attributions（域與此則無關，非錯誤） -->
          <icon-minus-circle-fill
            v-else-if="g.status === 'done' && g.hasResult === false"
            class="text-[#86909c]"
          />
          <icon-check-circle-fill
            v-else-if="g.status === 'done'"
            class="text-[rgb(var(--success-6))]"
          />
          <icon-close-circle-fill v-else class="text-[rgb(var(--danger-6))]" />
          <span class="ml-1 font-medium">{{ g.key }}</span>
        </template>

        <a-timeline class="pl-1 pt-2">
          <a-timeline-item
            v-for="(e, i) in g.entries"
            :key="i"
            :dot-color="LLM_DOT[e.kind] || '#86909c'"
            :label="fmtTs(e.ts)"
          >
            <div class="text-xs font-medium text-[#1d2129]">
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
                <LogJsonBlock :json="v" />
              </div>
            </template>

            <!-- Prompt 全文：System / User 預設皆展開（一眼看到全部；仍可手動收合） -->
            <a-collapse
              v-else-if="e.kind === 'llm_prompt'"
              :bordered="false"
              :default-active-key="['system', 'user']"
              class="mt-1"
            >
              <a-collapse-item key="system" header="System prompt">
                <pre class="max-h-80 overflow-auto whitespace-pre-wrap break-all text-xs">{{
                  e.data?.system
                }}</pre>
              </a-collapse-item>
              <a-collapse-item key="user" header="User prompt">
                <pre class="max-h-80 overflow-auto whitespace-pre-wrap break-all text-xs">{{
                  e.data?.user
                }}</pre>
              </a-collapse-item>
            </a-collapse>

            <!-- 輸出：原始回應全文（JSON 格式可解析 → 樹狀檢視；否則如實顯示原文）+ 用量摘要 -->
            <template v-else-if="e.kind === 'llm_response'">
              <LogJsonBlock
                v-if="tryParseRaw(e.data?.raw) ?? e.data?.parsed"
                :json="tryParseRaw(e.data?.raw) ?? e.data?.parsed"
              />
              <pre
                v-else
                class="mt-1 max-h-80 overflow-auto whitespace-pre-wrap break-all rounded bg-[#f0f9f0] p-1.5 text-xs"
                >{{ e.data?.raw }}</pre
              >
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
          <a-timeline-item v-if="g.status === 'running'" dot-color="#4080ff">
            <div class="flex items-center gap-1 text-xs text-[#86909c]">
              <icon-loading /> 等待回應…
            </div>
          </a-timeline-item>
          <a-timeline-item v-else-if="g.status === 'incomplete'" dot-color="#f53f3f">
            <div class="text-xs text-[#f53f3f]">
              此調用未取得回應（可能例外中斷 / 額度不足，詳見「流程」tab 的錯誤）
            </div>
          </a-timeline-item>
        </a-timeline>
      </a-tab-pane>
    </StickyTabs>
  </div>
</template>

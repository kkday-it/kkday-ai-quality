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
 * `:deep()` CSS——`.arco-tabs-content` 是 StickyTabs 內建的唯一捲動容器，LLM 調用 tab 左側
 * 掛錨點導航（`a-anchor`）與其並排、`scroll-container` 指向該容器（`getScrollEl()`），捲動範圍
 * 天然限定在 tab 列下方的右側內容區，不含 tab 列與導航自身，也不需要外層另包一層捲動容器。
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
import LlmCallTimeline from './LlmCallTimeline.vue';
import type { LogEntry } from './PrejudgeLogView.types';
import {
  DIALOGUE_ROLE_COLORS,
  DIALOGUE_ROLE_LABELS,
  POLARITY_COLOR,
  POLARITY_LABELS,
  schemaFor,
  TIER_LABELS,
} from '../constants';
import {
  fmtTs,
  LLM_KIND_LABEL,
  logEntryId,
  parseDialogue,
  scalarParams,
  tryParseRaw,
  type DialogueTurn,
} from '../utils';

const props = withDefaults(
  defineProps<{
    entries: LogEntry[];
    /** 是否仍在串流中（true 且 entries 為空時顯示等待占位）；歷史回看靜態 log 傳 false。 */
    streaming?: boolean;
  }>(),
  { streaming: false },
);

const stickyTabsRef = ref<InstanceType<typeof StickyTabs>>();
/** `StickyTabs` 內部唯一捲動容器（`.arco-tabs-content`），餵給左側錨點導航的 `scroll-container`。 */
const scrollEl = ref<HTMLElement | null>(null);
// entries 首次掛載 / 每次變動（串流新增條目）→ 重新取得捲動容器（idempotent）；
// 串流中另委由 StickyTabs 捲動當前可見 tab 的內容區到底，歷史回看（streaming=false）不自動捲。
watch(
  () => props.entries.length,
  async () => {
    await nextTick();
    scrollEl.value = stickyTabsRef.value?.getScrollEl() ?? null;
    if (props.streaming) stickyTabsRef.value?.scrollActiveToBottom();
  },
  { immediate: true },
);

const isLlm = (kind: string) => kind.startsWith('llm_');

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
        { attributions?: unknown } | undefined;
      // 只有 attribute 域回應才有 attributions 欄；is-array 判斷本身即排除 polarity 等無此欄的階段
      g.hasResult = Array.isArray(parsed?.attributions) ? parsed.attributions.length > 0 : null;
    }
  }
  return order.map((k) => map.get(k)!);
});

const activeTab = ref('__flow__');
/** 當前 active tab 若為某次 LLM 調用（非「流程」tab）→ 該調用的分組，供左側錨點導航渲染；
 * 流程 tab 是一般時間軸、不需要導航 → undefined。 */
const activeCallGroup = computed(() => callGroups.value.find((g) => g.key === activeTab.value));

/** 時間軸節點色（依 stage 語義；LLM 調用 tab 自身節點色見 `LlmCallTimeline`）。 */
const STAGE_DOT: Record<string, string> = {
  job: '#4080ff',
  item: '#14c9c9',
  db: '#00b42a',
  polarity: '#722ed1',
  attribute: '#722ed1',
};
const flowDot = (e: LogEntry) => (e.kind === 'error' ? '#f53f3f' : STAGE_DOT[e.stage] || '#86909c');

/** 流程 tab stage 機器碼 → 中文顯示標籤（entry 本身保留機器碼；未知碼原樣顯示）。 */
const FLOW_STAGE_LABELS: Record<string, string> = {
  job: '任務',
  item: '單筆',
  db: '落庫',
};

/** 「歸類完成」entry 附帶的單筆歸因 digest（後端 `_work_one` emit；欄位缺省安全）。 */
interface FindingDigest {
  polarity?: string;
  l1?: string;
  l2?: string;
  confidence?: number;
  tier?: string;
  summary?: string;
}
/** 取 entry 的歸因結果 digest 陣列（非「歸類完成」entry 回空陣列 → 不渲染結果塊）。 */
const findingsOf = (e: LogEntry): FindingDigest[] =>
  Array.isArray(e.data?.findings) ? (e.data?.findings as FindingDigest[]) : [];

/** 「開始判決」entry 的原文標題（source-schema `title`；評論類來源如 product_reviews 才有值）。 */
const titleOf = (e: LogEntry): string => String(e.data?.title || '');

/** 「開始判決」entry 的內容輪次：依來源 schema contentMode（conversations＝dialogue）解析 [ROLE]:
 * 前綴成輪次，同 AttributionList 渲染邏輯；非對話來源或解析失敗回 null → fallback 原樣全文。 */
const dialogueTurnsOf = (e: LogEntry): DialogueTurn[] | null => {
  const source = String(e.data?.source || '');
  if (schemaFor(source).contentMode !== 'dialogue') return null;
  return parseDialogue(String(e.data?.content || ''));
};

/** 信心數字按分層上色（同列表：auto_accept 綠 / jury 琥珀 / needs_review 紅）。 */
const CONF_TIER_CLASS: Record<string, string> = {
  auto_accept: 'text-[rgb(var(--success-6))]',
  jury: 'text-[rgb(var(--warning-6))]',
  needs_review: 'text-[rgb(var(--danger-6))]',
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

    <div v-else class="flex h-full min-h-0 gap-4">
      <!-- 左側掛錨點導航：僅 LLM 調用 tab 顯示，指向 StickyTabs 內部唯一的捲動容器。 -->
      <a-anchor
        v-if="activeCallGroup"
        class="w-32 shrink-0 pt-2"
        :scroll-container="scrollEl ?? undefined"
        :change-hash="false"
        :smooth="true"
        line-less
      >
        <a-anchor-link
          v-for="(e, i) in activeCallGroup.entries"
          :key="i"
          :href="`#${logEntryId(activeCallGroup.key, i)}`"
          :title="LLM_KIND_LABEL[e.kind] || e.kind"
        />
      </a-anchor>

      <StickyTabs
        ref="stickyTabsRef"
        v-model:active-key="activeTab"
        type="card-gutter"
        size="small"
        :lazy-load="true"
        class="min-w-0 flex-1"
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
              <div
                class="text-xs"
                :class="e.kind === 'error' ? 'text-[#f53f3f]' : 'text-[#4e5969]'"
              >
                <a-tag size="small" :color="e.kind === 'error' ? 'red' : 'gray'">
                  {{ FLOW_STAGE_LABELS[e.stage] || e.stage }}
                </a-tag>
                <span class="ml-1">{{ e.message }}</span>
                <!-- 歸類完成：逐筆歸因結果塊（傾向 + L1›L2 + 信心/分層 + 摘要），流程 tab 一目瞭然 -->
                <div v-if="findingsOf(e).length" class="mt-1 flex flex-col gap-1">
                  <div
                    v-for="(f, fi) in findingsOf(e)"
                    :key="fi"
                    class="rounded border border-[var(--color-border-1)] bg-[var(--color-fill-1)] px-2 py-1"
                  >
                    <div class="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                      <a-tag
                        v-if="f.polarity"
                        size="small"
                        :color="POLARITY_COLOR[f.polarity] || 'gray'"
                      >
                        {{ POLARITY_LABELS[f.polarity] || f.polarity }}
                      </a-tag>
                      <template v-if="f.l1 || f.l2">
                        <span class="font-medium text-[rgb(var(--primary-6))]">{{ f.l1 }}</span>
                        <template v-if="f.l2">
                          <span class="text-[#86909c]">›</span>
                          <span>{{ f.l2 }}</span>
                        </template>
                      </template>
                      <span v-else class="text-[#86909c]">未歸因</span>
                      <span
                        v-if="typeof f.confidence === 'number'"
                        class="font-semibold"
                        :class="CONF_TIER_CLASS[f.tier || ''] || ''"
                      >
                        {{ f.confidence.toFixed(2) }}
                      </span>
                      <span
                        v-if="f.tier"
                        class="rounded bg-[var(--color-fill-2)] px-1 text-[11px] text-[#4e5969]"
                      >
                        {{ TIER_LABELS[f.tier] || f.tier }}
                      </span>
                    </div>
                    <div v-if="f.summary" class="mt-0.5 font-medium text-[var(--color-text-1)]">
                      {{ f.summary }}
                    </div>
                  </div>
                </div>
                <div v-else-if="e.data?.content" class="mt-0.5">
                  <div v-if="titleOf(e)" class="font-medium text-[var(--color-text-1)]">
                    {{ titleOf(e) }}
                  </div>
                  <!-- 進線對話：按 [ROLE]: 前綴解析輪次（角色 tag + 該輪文字），比照 AttributionList
                     歸因列表一眼辨發話方；非對話來源或解析失敗 fallback 原樣加引號全文 -->
                  <div v-if="dialogueTurnsOf(e)" class="flex flex-col gap-1">
                    <div
                      v-for="(t, ti) in dialogueTurnsOf(e)"
                      :key="ti"
                      class="break-all text-[#86909c]"
                    >
                      <a-tag
                        v-if="t.role"
                        size="small"
                        :color="DIALOGUE_ROLE_COLORS[t.role] || 'gray'"
                        class="mr-1"
                        >{{ DIALOGUE_ROLE_LABELS[t.role] || t.role }}</a-tag
                      >
                      <span class="whitespace-pre-wrap">{{ t.text }}</span>
                    </div>
                  </div>
                  <div v-else class="break-all text-[#86909c]">「{{ e.data.content }}」</div>
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

          <!-- 一次 LLM 調用的時間軸內容（請求／Prompt／回應）：抽為 LlmCallTimeline 供本檔各調用 tab
             復用；左側掛錨點導航見上方（本檔統一持有，點擊平滑捲動定位，捲動時 Arco Anchor
             內建 scrollspy 自動同步高亮當前節點）。 -->
          <LlmCallTimeline :entries="g.entries" :call-key="g.key" :status="g.status" />
        </a-tab-pane>
      </StickyTabs>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 執行日誌視圖（分組 wrapper）：依 entries 的 `item_id` 自動適應——
 * - 單組（單筆初判／舊快照無 item_id／沙盒單挑）：直接渲染 `PrejudgeLogTabs`，與既往完全一致。
 * - 多組（批量 ≥2 筆）：主從式——左欄「整體流程」評論清單（每筆狀態一目了然＝批量整體視角），
 *   點任一筆 → 右側以同一份 `PrejudgeLogTabs` 只渲染該筆條目（單筆密度的 流程＋polarity＋C-N
 *   tabs＝快速下鑽單筆執行日誌）；job 級事件（任務啟動參數等）收在「整體流程」偽列。
 * 串流時預設自動跟隨「最新有動靜」的評論，使用者手動點選即釘住不再跳。
 * 三個消費端（確認初判分類抽屜／PrejudgeLogDrawer／PromptSandboxDrawer）零改動自動升級。
 */
import { computed, ref, watch } from 'vue';
import {
  IconCheckCircleFill,
  IconCloseCircleFill,
  IconLoading,
  IconMinusCircleFill,
} from '@arco-design/web-vue/es/icon';
import PrejudgeLogTabs from './PrejudgeLogTabs.vue';
import type { LogEntry } from './PrejudgeLogView.types';

const props = withDefaults(
  defineProps<{
    entries: LogEntry[];
    /** 是否仍在串流中（true 且 entries 為空時顯示等待占位）；歷史回看靜態 log 傳 false。 */
    streaming?: boolean;
  }>(),
  { streaming: false },
);

/** job 級事件的偽分組鍵（entry 無 item_id 者歸此；亦是「整體流程」列的選取值）。 */
const JOB_KEY = '__job__';

/** 單一評論的日誌分組（狀態自條目派生，供左欄清單一眼掃）。 */
interface ItemGroup {
  id: string;
  entries: LogEntry[];
  /** 「開始初判」entry 的原文標題（可空）。 */
  title: string;
  status: 'running' | 'done' | 'failed' | 'incomplete';
  /** 歸類完成的歸因筆數（未完成＝null；0＝未歸因）。 */
  findings: number | null;
  /** 評論傾向（歸類完成 digest 首筆 polarity；未完成＝''）——底色區分比照導出表格。 */
  polarity: string;
}

/** 按 item_id 分組（保持首次出現序＝派工序）；無 item_id 條目歸 job 組。 */
const groupMap = computed(() => {
  const map = new Map<string, LogEntry[]>();
  for (const e of props.entries) {
    const key = e.item_id || JOB_KEY;
    const list = map.get(key);
    if (list) list.push(e);
    else map.set(key, [e]);
  }
  return map;
});

const itemGroups = computed<ItemGroup[]>(() => {
  const out: ItemGroup[] = [];
  for (const [id, list] of groupMap.value) {
    if (id === JOB_KEY) continue;
    const failed = list.some((e) => e.kind === 'error');
    const doneEntry = list.find((e) => e.stage === 'item' && Array.isArray(e.data?.findings));
    const start = list.find((e) => e.data?.title);
    out.push({
      id,
      entries: list,
      title: String(start?.data?.title || ''),
      status: failed ? 'failed' : doneEntry ? 'done' : props.streaming ? 'running' : 'incomplete',
      findings: doneEntry ? (doneEntry.data?.findings as unknown[]).length : null,
      polarity: String(
        (doneEntry?.data?.findings as { polarity?: string }[] | undefined)?.[0]?.polarity || '',
      ),
    });
  }
  return out;
});

/** 批量（≥2 筆）才切主從式；單組維持既往單視圖（含舊快照/單筆情境零視覺差異）。 */
const grouped = computed(() => itemGroups.value.length >= 2);

/** 左欄頂部彙總（完成/失敗/進行中）。 */
const summary = computed(() => {
  const g = itemGroups.value;
  return {
    total: g.length,
    done: g.filter((x) => x.status === 'done').length,
    failed: g.filter((x) => x.status === 'failed').length,
    running: g.filter((x) => x.status === 'running').length,
  };
});

const selectedId = ref(JOB_KEY);
/** 使用者手動點選後釘住，串流不再自動跳到最新動靜的評論。 */
const pinned = ref(false);
const select = (id: string) => {
  selectedId.value = id;
  pinned.value = true;
};
// 串流跟隨：未釘住時自動選「最新有動靜」的評論（最後一條帶 item_id 的 entry 所屬組）；
// 尚無 item 事件（剛啟動只有 job 級）時停在整體流程列。
watch(
  () => props.entries.length,
  () => {
    if (pinned.value || !props.streaming) return;
    for (let i = props.entries.length - 1; i >= 0; i--) {
      const iid = props.entries[i].item_id;
      if (iid) {
        selectedId.value = iid;
        return;
      }
    }
  },
  { immediate: true },
);

const selectedEntries = computed(
  () => groupMap.value.get(selectedId.value) ?? groupMap.value.get(JOB_KEY) ?? [],
);
/** 右側 tabs 的串流旗標：已完成/失敗的評論不再視為串流中（其未回應調用應顯示中斷而非轉圈）。 */
const selectedStreaming = computed(() => {
  if (!props.streaming) return false;
  const g = itemGroups.value.find((x) => x.id === selectedId.value);
  return g ? g.status === 'running' : true; // 整體流程列跟隨全域串流
});

/** 傾向 → 列底色＋左色條（比照導出表格整列底色語義；選中另疊 primary 底、色條保留辨識）。 */
const POLARITY_ROW_CLASS: Record<string, string> = {
  positive: 'border-l-[rgb(var(--success-4))] bg-[rgb(var(--success-1))]',
  neutral: 'border-l-[#c9cdd4] bg-[var(--color-fill-1)]',
  negative: 'border-l-[rgb(var(--danger-4))] bg-[rgb(var(--danger-1))]',
};

const STATUS_LABELS: Record<ItemGroup['status'], string> = {
  running: '進行中',
  done: '完成',
  failed: '失敗',
  incomplete: '中斷',
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

    <!-- 單組：既往單視圖原樣（單筆/舊快照/沙盒單挑） -->
    <PrejudgeLogTabs v-else-if="!grouped" :entries="entries" :streaming="streaming" />

    <!-- 多組（批量）：主從式——左欄整體流程清單、右側選中評論的完整日誌 -->
    <div v-else class="flex h-full min-h-0 gap-3">
      <div class="flex w-60 shrink-0 flex-col overflow-hidden rounded border">
        <div
          class="flex flex-none flex-wrap items-center gap-x-2 border-b bg-[var(--color-fill-1)] px-2 py-1.5 text-xs text-[#4e5969]"
        >
          <span>共 {{ summary.total }}</span>
          <span class="text-[rgb(var(--success-6))]">✓ {{ summary.done }}</span>
          <span v-if="summary.failed" class="text-[rgb(var(--danger-6))]">
            ✗ {{ summary.failed }}
          </span>
          <span v-if="summary.running" class="text-[rgb(var(--primary-6))]">
            ⟳ {{ summary.running }}
          </span>
        </div>
        <div class="min-h-0 flex-1 overflow-auto">
          <!-- 整體流程偽列：job 級事件（任務啟動參數 / job error） -->
          <div
            class="cursor-pointer border-b px-2 py-1.5 text-xs"
            :class="
              selectedId === JOB_KEY
                ? 'bg-[rgb(var(--primary-1))] text-[rgb(var(--primary-6))]'
                : 'text-[#4e5969] hover:bg-[var(--color-fill-1)]'
            "
            @click="select(JOB_KEY)"
          >
            整體流程
          </div>
          <div
            v-for="g in itemGroups"
            :key="g.id"
            class="cursor-pointer border-b border-l-4 px-2 py-1.5"
            :class="[
              POLARITY_ROW_CLASS[g.polarity] ||
                'border-l-transparent hover:bg-[var(--color-fill-1)]',
              selectedId === g.id ? '!bg-[rgb(var(--primary-1))]' : 'hover:brightness-[0.97]',
            ]"
            :title="`${g.id} ${g.title}·${STATUS_LABELS[g.status]}`"
            @click="select(g.id)"
          >
            <div class="flex items-center gap-1 text-xs">
              <icon-loading v-if="g.status === 'running'" class="shrink-0 text-[#4080ff]" />
              <icon-close-circle-fill
                v-else-if="g.status === 'failed'"
                class="shrink-0 text-[rgb(var(--danger-6))]"
              />
              <!-- 完成但 0 筆歸因＝未歸因（灰），有歸因＝綠勾 -->
              <icon-minus-circle-fill
                v-else-if="g.status === 'done' && g.findings === 0"
                class="shrink-0 text-[#86909c]"
              />
              <icon-check-circle-fill
                v-else-if="g.status === 'done'"
                class="shrink-0 text-[rgb(var(--success-6))]"
              />
              <icon-minus-circle-fill v-else class="shrink-0 text-[#c9cdd4]" />
              <span class="font-mono text-[var(--color-text-1)]">{{ g.id }}</span>
              <!-- 歸因筆數：常態 1 筆不標（噪音），僅多歸因（≥2）才顯示——那才需要被注意 -->
              <span
                v-if="(g.findings ?? 0) > 1"
                class="ml-auto shrink-0 text-[11px] text-[#86909c]"
              >
                {{ g.findings }} 歸因
              </span>
            </div>
            <div v-if="g.title" class="mt-0.5 truncate text-[11px] text-[#86909c]">
              {{ g.title }}
            </div>
          </div>
        </div>
      </div>
      <!-- :key 換組即重掛（tab 回到流程、捲動歸零），單筆密度與單列初判體驗一致 -->
      <PrejudgeLogTabs
        :key="selectedId"
        :entries="selectedEntries"
        :streaming="selectedStreaming"
        class="min-w-0 flex-1"
      />
    </div>
  </div>
</template>

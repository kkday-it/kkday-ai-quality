<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { IconLoading } from '@arco-design/web-vue/es/icon';
import { getPrejudgeRunLog } from '@/api';
import PrejudgeLogView from './PrejudgeLogView.vue';
import type { LogEntry } from './PrejudgeLogView.types';

// 歸因歷史回看 LLM 執行日誌快照：GET 落庫快照一次性載入，靜態渲染（無 SSE）。
// 唯一消費者＝ AttributionHistoryDrawer（歸因歷史「查看 LLM 日誌」入口開）。

const props = defineProps<{
  visible: boolean;
  /** 抽屜開啟且非空時讀落庫快照。 */
  jobId: string;
  /** 只看此評論（source_id）：自單一評論視角（歸因歷史）進入時過濾批量快照，直達該筆日誌；
   *  空＝整批視角（批量抽屜終態摘要卡進入）。舊快照條目無 item_id → 過濾不到就回退全量。 */
  itemId?: string;
}>();
const emit = defineEmits<{ (e: 'update:visible', v: boolean): void }>();

const entries = ref<LogEntry[]>([]);
const loadingHistory = ref(false);
const streamError = ref('');

const _openHistory = async (jid: string) => {
  loadingHistory.value = true;
  try {
    const r = await getPrejudgeRunLog(jid);
    entries.value = r.entries;
  } catch (e: any) {
    streamError.value =
      (e?.message || '此任務無執行日誌快照') +
      '——大批量任務（逐筆日誌僅小批量收集）或啟用日誌前的舊初判皆屬正常，不代表未執行；執行結果與費用見「初判紀錄」。';
  } finally {
    loadingHistory.value = false;
  }
};

const _open = (jid: string) => {
  entries.value = [];
  streamError.value = '';
  showAll.value = false;
  void _openHistory(jid);
};

watch(
  () => [props.visible, props.jobId] as const,
  ([v, jid]) => {
    if (v && jid) _open(jid);
  },
  { immediate: true },
);

/** 快照是否帶逐評論蓋章（item_id）；舊快照（機制上線前）為 false，走 message 回退過濾。 */
const stamped = computed(() => entries.value.some((e) => e.item_id));

/** itemId 過濾後的條目（含 job 級事件供脈絡）。
 *  新快照：按 item_id 精準過濾（流程＋LLM 三段完整聚焦本評論）。
 *  舊快照：流程條目按 data.source_id / message 內嵌 id 過濾；LLM 條目無法歸屬單一評論
 *  → 排除（混排誤導比缺席更糟，見 legacyNote 提示）；完全過濾不到才回退全量。 */
/** 舊快照聚焦時的逃生口：使用者主動切看整批原始混排。 */
const showAll = ref(false);

/** 聚焦本評論的過濾結果（獨立於 showAll 開關，供 shownEntries 與 legacyNote 共用）；
 *  null＝不過濾（無 itemId／過濾不到任何本評論條目時回退全量）。
 *  新快照：按 item_id 精準過濾（流程＋LLM 三段完整）。
 *  舊快照：流程條目按 data.source_id / message 內嵌 id（帶邊界防撞號）過濾；LLM 條目
 *  無法歸屬單一評論 → 排除（混排誤導比缺席更糟，缺席原因見 legacyNote 提示）。 */
const focusedEntries = computed<LogEntry[] | null>(() => {
  const iid = props.itemId;
  if (!iid) return null;
  if (stamped.value) {
    const mine = entries.value.filter((e) => !e.item_id || e.item_id === iid);
    return mine.some((e) => e.item_id) ? mine : null;
  }
  const esc = iid.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const idRe = new RegExp(`(^|\\D)${esc}(\\D|$)`);
  const mine = entries.value.filter(
    (e) =>
      e.stage === 'job' ||
      (!e.kind.startsWith('llm_') &&
        (String(e.data?.source_id || '') === iid || idRe.test(e.message))),
  );
  return mine.some((e) => e.stage !== 'job') ? mine : null;
});

const shownEntries = computed(() =>
  showAll.value ? entries.value : (focusedEntries.value ?? entries.value),
);

/** 舊快照且成功聚焦 → 提示 LLM 調用為何缺席＋整批/聚焦切換（新初判後即完整）。 */
const legacyNote = computed(
  () =>
    !stamped.value &&
    focusedEntries.value !== null &&
    focusedEntries.value.length < entries.value.length,
);
</script>

<template>
  <a-drawer
    :visible="visible"
    :width="880"
    :footer="false"
    unmount-on-close
    :body-style="{
      display: 'flex',
      flexDirection: 'column',
      overflow: 'hidden',
      padding: '12px 16px',
    }"
    @update:visible="(v: boolean) => emit('update:visible', v)"
  >
    <template #title>
      <span>LLM 執行日誌</span>
      <a-tag size="small" class="ml-2 font-mono">{{ jobId }}</a-tag>
      <a-tag color="gray" size="small" class="ml-1">歷史快照</a-tag>
      <a-tag v-if="itemId" size="small" class="ml-1 font-mono">評論 {{ itemId }}</a-tag>
    </template>

    <a-alert v-if="streamError" type="info" class="mb-2">{{ streamError }}</a-alert>
    <a-alert v-if="legacyNote && !showAll" type="info" class="mb-2">
      此快照產生於逐評論標記機制之前：流程已按本評論過濾，但 LLM 調用（polarity / C-N）無法歸屬
      單一評論故未顯示——對本評論「重新初判」一次即可取得完整逐評論日誌。
      <a-button size="mini" type="text" @click="showAll = true">查看整批原始日誌</a-button>
    </a-alert>
    <a-alert v-if="legacyNote && showAll" type="info" class="mb-2">
      整批原始日誌（未過濾，含全部評論的混排條目）。
      <a-button size="mini" type="text" @click="showAll = false">回到本評論視角</a-button>
    </a-alert>

    <div v-if="loadingHistory" class="flex items-center gap-2 py-6 text-xs text-[#86909c]">
      <icon-loading /> 載入歷史日誌快照…
    </div>
    <!-- 捲動已下沉至 PrejudgeLogView 內部（StickyTabs 的 .arco-tabs-content 為唯一捲動容器，
         tab 列固定、左側掛錨點導航與其並排）；本層僅需給出 bounded 高度，不再自行 overflow-auto。 -->
    <div v-else class="min-h-0 flex-1 overflow-hidden">
      <PrejudgeLogView :entries="shownEntries" :streaming="false" />
    </div>
  </a-drawer>
</template>

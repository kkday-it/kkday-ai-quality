<script setup lang="ts">
/**
 * 滿高表格佈局（全局公共元件）：撐滿父容器高度的卡片，內部「表頭（#toolbar）固定 →
 * 中間內容區滾動 → 表尾固定」。收斂各列表頁重複的 flex 樣板 / 表格默認 / 分頁配置。
 *
 * 兩種模式（依 `data` prop 自動切換）：
 *
 * 1. **內建表格模式**（傳 `data` 即啟用，首選）：內部渲染 a-table，自動打底
 *    `TABLE_DEFAULTS` + 滿高滾動（`min-h-0 flex-1` + `scroll.y='100%'`）+ 分頁 preset。
 *    其餘 attrs（columns / row-key / expandable / row-selection / @sorter-change…）與
 *    非佈局 slots（#columns / #expand-row / 自訂 cell…）全數透傳進 a-table，個別可覆蓋默認。
 *    - 分頁：`pagination` 傳 `'standard'`（預設）/ `'with-all'`（含「全部」，限小表）/
 *      `false` / 自訂物件。
 *    - 伺服器分頁：加 `server :total v-model:page v-model:page-size @change="load"`，
 *      元件自動組 pagination 與換頁 handlers（換 pageSize 自動回第 1 頁），變更後 emit change。
 *    - 三態：`loading` 走 a-table 內建 spin（保留表頭）；`error` 於表上方顯示 alert
 *      （不遮既有資料）；空資料走 a-table 內建 empty（`emptyText` 自訂文案）。
 * 2. **純佈局模式**（不傳 `data`，向後相容）：預設插槽自行擺放內容（a-table 或任意
 *    overflow 區塊），a-table 需自帶 `class="min-h-0 flex-1"` + `:scroll="{ y: '100%' }"`。
 *
 * 前置：需父鏈提供確定高度——頁面根元素加 `h-full`（AppShell 內容區已是 flex-1 撐高）；
 * 抽屜 / 彈窗等非 flex 父容器改傳 `full-height`（根加 h-full，免手包 wrapper）。
 *
 * @slot extra - 透傳 CardSection #extra：卡片標題右上角（放少量篩選/操作；不提供則顯示 hint）
 * @slot toolbar - 卡片內、內容區上方固定的表頭（通常放較寬的篩選列）
 * @slot default - 純佈局模式的中間可滾內容區（內建表格模式下忽略）
 * @slot footer - 固定於底部的表尾（放自訂分頁/操作列；內建分頁時免用）
 * @slot [其餘] - 內建表格模式下全數轉發進 a-table（#columns / #expand-row / #empty / 自訂 cell…）
 */
import { computed, ref, useAttrs, useSlots } from 'vue';
import {
  ALL_PAGINATION,
  DEFAULT_PAGE_SIZE,
  PAGINATION_WITH_ALL,
  TABLE_DEFAULTS,
} from '@/constants';
import CardSection from './CardSection.vue';

defineOptions({ inheritAttrs: false });

const props = withDefaults(
  defineProps<{
    /** 卡片標題（透傳 CardSection） */
    title?: string;
    /** 右上灰字說明（透傳 CardSection） */
    hint?: string;
    /** 完整說明（多行 ⓘ popover；透傳 CardSection） */
    desc?: string;
    /** 非 flex 父容器（抽屜 / 彈窗）撐滿：根加 h-full */
    fullHeight?: boolean;
    /** 表格資料；提供即啟用內建表格模式 */
    data?: Record<string, unknown>[];
    /** 載入中（a-table 內建 spin，保留表頭） */
    loading?: boolean;
    /** 錯誤訊息（非空即於表上方顯示 error alert，不遮資料） */
    error?: string;
    /** 空資料文案（a-table 內建 empty 的 description） */
    emptyText?: string;
    /** 分頁 preset（standard / with-all / false）或自訂 pagination 物件 */
    pagination?: 'standard' | 'with-all' | false | Record<string, unknown>;
    /** 伺服器分頁：自動組 current/pageSize/total 與換頁 handlers，變更後 emit change */
    server?: boolean;
    /** 總筆數（server 模式） */
    total?: number;
  }>(),
  {
    pagination: 'standard',
    title: undefined,
    hint: undefined,
    desc: undefined,
    data: undefined,
    error: undefined,
    emptyText: undefined,
    total: undefined,
  },
);

const emit = defineEmits<{
  /** server 模式下換頁 / 換每頁筆數後觸發（models 已更新，直接重載即可） */
  (e: 'change'): void;
}>();

/** 目前頁碼（server 模式，v-model:page） */
const page = defineModel<number>('page', { default: 1 });
/** 每頁筆數（server 模式，v-model:page-size） */
const pageSize = defineModel<number>('pageSize', { default: DEFAULT_PAGE_SIZE });

const attrs = useAttrs();
const slots = useSlots();

/** 傳 data 即為內建表格模式；否則純佈局（預設插槽自理）。 */
const tableMode = computed(() => props.data != null);

/** 佈局自有 slots，不轉發進 a-table。 */
const LAYOUT_SLOTS = ['default', 'toolbar', 'footer', 'extra', 'title'];
const forwardSlots = computed(() => Object.keys(slots).filter((n) => !LAYOUT_SLOTS.includes(n)));

/** 分頁組裝：preset / 自訂物件 → server 模式再疊 current / pageSize / total。 */
const pagObj = computed(() => {
  if (props.pagination === false) return false;
  const base =
    props.pagination === 'with-all'
      ? PAGINATION_WITH_ALL
      : props.pagination === 'standard'
        ? ALL_PAGINATION
        : props.pagination;
  if (!props.server) return base;
  return { ...base, current: page.value, pageSize: pageSize.value, total: props.total ?? 0 };
});

/** 滿高滾動默認：y='100%'；呼叫端 :scroll 淺合併可覆蓋（如加 x 橫向）。 */
const scrollMerged = computed(() => ({
  y: '100%',
  ...((attrs.scroll as Record<string, unknown>) ?? {}),
}));

/** server 模式換頁 handlers（換 pageSize 回第 1 頁）；client 模式交 a-table 內建。 */
const onPageChange = (p: number) => {
  page.value = p;
  emit('change');
};
const onPageSizeChange = (s: number) => {
  pageSize.value = s;
  page.value = 1;
  emit('change');
};
const serverHandlers = computed(() =>
  props.server ? { pageChange: onPageChange, pageSizeChange: onPageSizeChange } : {},
);

/** 內部 a-table 實例（需存取表格 DOM 時：`layoutRef.value?.tableRef?.$el`）。 */
const tableRef = ref<{ $el: HTMLElement } | null>(null);
defineExpose({ tableRef });

// body 撐高為 flex 直欄的固定樣式（呼叫端不再手抄；對齊 Analytics 既有做法）
const FILL_BODY = {
  flex: '1',
  minHeight: '0',
  display: 'flex',
  flexDirection: 'column',
} as const;
</script>

<template>
  <CardSection
    :title="title"
    :hint="hint"
    :desc="desc"
    class="flex min-h-0 flex-1 flex-col"
    :class="fullHeight ? 'h-full' : ''"
    :body-style="FILL_BODY"
    v-bind="tableMode ? {} : attrs"
  >
    <template v-if="$slots.extra" #extra><slot name="extra" /></template>
    <div v-if="$slots.toolbar" class="mb-2 shrink-0"><slot name="toolbar" /></div>

    <template v-if="tableMode">
      <a-alert v-if="error" type="error" class="mb-2 shrink-0">{{ error }}</a-alert>
      <a-table
        ref="tableRef"
        v-bind="{ ...TABLE_DEFAULTS, ...attrs }"
        :data="data"
        :loading="loading"
        :pagination="pagObj"
        :scroll="scrollMerged"
        class="min-h-0 flex-1"
        v-on="serverHandlers"
      >
        <template v-for="name in forwardSlots" :key="name" #[name]="slotProps">
          <slot :name="name" v-bind="slotProps ?? {}" />
        </template>
        <template v-if="emptyText && !$slots.empty" #empty>
          <a-empty :description="emptyText" />
        </template>
      </a-table>
    </template>
    <slot v-else />

    <div v-if="$slots.footer" class="mt-2 shrink-0"><slot name="footer" /></div>
  </CardSection>
</template>

<style scoped>
/* Arco a-table 首尾固定補丁：.arco-spin 雖是 flex 直欄（height:100%），但 .arco-table-container
   預設無 flex:1 → 列數不滿一屏時分頁跟著內容浮起、卡片底留空。utility 與元件 prop 皆觸不到
   此內層結構，故以 :deep 讓中段列表區撐滿滾動、分頁固定釘底（內層巢狀表無分頁不受影響）。 */
:deep(.arco-table > .arco-spin > .arco-table-container) {
  flex: 1;
  min-height: 0;
}
:deep(.arco-table > .arco-spin > .arco-table-pagination) {
  flex-shrink: 0;
}
</style>

<script setup lang="ts">
/**
 * `a-tabs` 公共包裝：內建「tab 列固定、內容捲動」行為（見 .claude/rules/frontend-vue.md
 * 「Tabs 切換展示」章節）——任何 tab 切換展示一律用本元件取代裸 `a-tabs`，不再各處手抄
 * `:deep()` CSS（曾在 PrejudgeLogView.vue 各自實作一份，現抽出避免重複維護）。
 *
 * 透明轉發：不宣告任何 props/emits，`$attrs`（含 `v-model:active-key`、`type`、`size` 等一切
 * a-tabs 原生 props/events）直接 `v-bind` 到內部 a-tabs；預設 slot 透傳，`<a-tab-pane>` 寫法
 * 與直接用 `a-tabs` 完全一致，替換零學習成本。
 *
 * 消費端前提：根元素需在有實際高度的容器內（drawer 走 `:body-style` 撐滿、頁面走 `h-full`），
 * 本元件才能 `flex:1` 撐滿並讓內容區正確捲動——內容區（`.arco-tabs-content`）恆為**唯一**捲動
 * 容器，tab 列固定不動。消費端若需要「內容旁再掛一份跟這個捲動容器同步的導航」（如 LLM 執行
 * 日誌的左側錨點導航），用 `getScrollEl()` 取得該捲動元素本身餵給 `a-anchor` 的
 * `scroll-container`，不要另外包一層外層捲動容器（見 `PrejudgeLogView`）。
 *
 * 每 tab 獨立捲動位置：`.arco-tabs-content` 在 `:lazy-load="true"` 下是**同一個** DOM 節點被
 * 所有 tab 共用（切 tab 只換內部子節點），若不介入，切走再切回會維持舊 scrollTop、切到「沒捲過
 * 的新 tab」也會沿用上一個 tab 的捲動位置——不是使用者預期的「每個 tab 各自獨立記住自己的捲動
 * 位置，沒捲過的預設在頂部」。本元件內部用 `active-key`（消費端必走 `v-model:active-key`）記錄
 * 每個 key 最後的 scrollTop：切走前存舊 tab 位置、切入後（等 lazy-load 掛載新內容）還原新 tab
 * 位置（未存過則回頂部），消費端不需要、也不應該自己處理。
 */
import { nextTick, ref, useAttrs, watch } from 'vue';

const attrs = useAttrs();
const root = ref<HTMLElement>();

/** `.arco-tabs-content`：:lazy-load 下同時只有 active pane 掛載，容器內僅一個此節點存在，
 * 免消費端自行得知 Arco 內部 class 名稱。 */
const getScrollEl = (): HTMLElement | null =>
  root.value?.querySelector<HTMLElement>('.arco-tabs-content') ?? null;

// key → 最後捲動位置；plain Map 即可（不需響應式，只在 tab 切換這個明確時間點讀寫）。
const scrollPositions = new Map<string, number>();

watch(
  () => attrs.activeKey as string | number | undefined,
  async (newKey, oldKey) => {
    // watch 預設 pre-flush：此刻 DOM 尚未因 activeKey 變更而重繪，getScrollEl() 抓到的仍是切走前
    // 那個 tab 的內容，此時讀到的 scrollTop 正是該 tab 離開當下的捲動位置。
    if (oldKey !== undefined) {
      const el = getScrollEl();
      if (el) scrollPositions.set(String(oldKey), el.scrollTop);
    }
    if (newKey === undefined) return;
    await nextTick(); // 等新 tab 的內容（lazy-load）掛載完成
    const el = getScrollEl();
    if (el) el.scrollTop = scrollPositions.get(String(newKey)) ?? 0;
  },
);

defineExpose({
  /** 目前 tab 內容的捲動容器（`.arco-tabs-content`）。 */
  getScrollEl,
  /** 捲動當前可見 tab 的內容區到底（串流新增條目時呼叫）。 */
  scrollActiveToBottom: (): void => {
    const scroller = getScrollEl();
    scroller?.scrollTo({ top: scroller.scrollHeight });
  },
});
</script>

<template>
  <div ref="root" class="sticky-tabs">
    <a-tabs v-bind="$attrs">
      <slot />
    </a-tabs>
  </div>
</template>

<style scoped>
.sticky-tabs {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}
.sticky-tabs :deep(.arco-tabs) {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0; /* 允許下方 content 收縮觸發內部捲動，而非撐爆父層 */
}
.sticky-tabs :deep(.arco-tabs-nav) {
  flex-shrink: 0; /* tab 列恆常可見，不隨內容捲走（Arco 預設已是 0，此處顯式鎖定防版本升級跑掉） */
}
.sticky-tabs :deep(.arco-tabs-content) {
  flex: 1;
  min-height: 0;
  overflow-x: hidden; /* 維持 Arco 原生水平 clip（多 pane 並排 + height:0/auto 切換機制），不可開放 */
  overflow-y: auto; /* 僅此層捲動，tab 列不隨內容位移 */
}
</style>

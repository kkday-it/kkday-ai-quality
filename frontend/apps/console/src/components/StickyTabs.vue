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
 * 本元件才能 `flex:1` 撐滿並讓內容區正確捲動。
 */
import { ref } from 'vue';

const root = ref<HTMLElement>();

defineExpose({
  /** 捲動當前可見 tab 的內容區到底（串流新增條目時呼叫）；:lazy-load 下同時只有 active pane
   * 掛載，故容器內僅一個 `.arco-tabs-content` 存在，免消費端自行得知 Arco 內部 class 名稱。 */
  scrollActiveToBottom: (): void => {
    const scroller = root.value?.querySelector<HTMLElement>('.arco-tabs-content');
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

<script setup lang="ts">
import { watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { BdTagVerticalSettingsPanel, NoteTypePanel } from '@/features/judge/components';
import { StickyTabs } from '@/components';

// 🗂️ 分類與選項＝「業務自己增減的分類與選項清單」，與 ⚙️ 配置（LLM／QC 連線／資料匯出入＝
// 系統配置與維運）分屬兩類，故拆成兩個抽屜、兩個 topbar 入口。原本五個 tab 擠在配置抽屜裡
// 已溢出到要用箭頭翻頁。判準：這個 tab 改的是「業務語彙」還是「系統怎麼跑」。
// 命名刻意不用「主檔／基礎資料／值域」——那些講的是資料的身分（DB 術語），業務看了不知道
// 點進去有什麼；「分類與選項」直接對應畫面內容，且兩詞覆蓋兩種型態：
//   分類＝BD TAG → PM/Vertical 的對照；選項＝可增減的枚舉清單。
// 未來要加的 建議行動／責任方／嚴重度 皆屬後者，名稱不必再改。
// ⚠️ 不叫「分類體系」：本專案 Taxonomy 已專指 C-1~C-6 歸因分類，會撞名。
//   vertical  ＝ BD 代碼 ↔ PM／Vertical 對照（版本化）
//   note-type ＝ 備註互動類型值域（原「判決值域」四軸，其餘三軸零消費者已於 f2a91c7b4d08 清退）
//
// ⚠️ 標題與 tab 帶 emoji 是刻意與 topbar 的 ⚙️ 配置 對齊（兩者是並列的同級入口，視覺要成對）；
//    這是 frontend-vue.md「新抽屜不要跟進 SettingsDrawer emoji 寫法」的**具名例外**，
//    非 topbar 入口的一般抽屜仍不得加 emoji。
// ⚠️ 新增 tab 時務必同步補下方 watch 分支：少一支不會報錯，只是深連結靜默失效。
type ValueDomainTab = 'vertical' | 'note-type';

const visible = defineModel<boolean>('visible', { default: false });
const tab = defineModel<ValueDomainTab>('tab', { default: 'vertical' });

const route = useRoute();
const router = useRouter();

const syncQuery = (t: ValueDomainTab) => router.replace({ query: { ...route.query, master: t } });
const clearQuery = () => {
  if (!route.query.master) return;
  const q = { ...route.query };
  delete q.master;
  router.replace({ query: q });
};

watch(visible, (v) => (v ? syncQuery(tab.value) : clearQuery()));
watch(tab, (t) => {
  if (visible.value) syncQuery(t);
});

// 同時吃舊的 ?settings=vertical|note-type|verdict-dimension——這兩個 tab 2026-08-11 前住在配置抽屜，
// 舊深連結不該因為搬家而失效（純別名，不是新功能，故不留 tombstone 疑慮）。
watch(
  () => [route.query.master, route.query.settings],
  ([m, s]) => {
    const v = m || s;
    if (v === 'vertical') {
      tab.value = 'vertical';
      visible.value = true;
    } else if (v === 'note-type' || v === 'verdict-dimension') {
      tab.value = 'note-type';
      visible.value = true;
    }
  },
  { immediate: true },
);
</script>

<template>
  <a-drawer
    v-model:visible="visible"
    placement="right"
    title="🗂️ 分類與選項"
    :width="640"
    :footer="false"
    unmount-on-close
    :body-style="{ display: 'flex', flexDirection: 'column', overflow: 'hidden' }"
  >
    <StickyTabs v-model:active-key="tab">
      <a-tab-pane key="vertical" title="🧭 商品垂直分類">
        <BdTagVerticalSettingsPanel :active="tab === 'vertical'" />
      </a-tab-pane>
      <a-tab-pane key="note-type" title="🏷️ 備註類型">
        <NoteTypePanel :active="tab === 'note-type'" />
      </a-tab-pane>
    </StickyTabs>
  </a-drawer>
</template>

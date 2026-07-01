<script setup lang="ts">
import { watch, onMounted } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { LlmConnectionsPanel, QcConnectionsPanel } from '@/features/settings/pages';
import { ProductVerticalSettingsPanel } from '@/features/judge/components';

// ⚙️ 配置抽屜＝「公共配置」：右滑疊加，三分頁 —— 🤖 LLM 模型連線 ｜ 🗄️ QC DB 連線 ｜ 🧭 商品垂直分類。
// 前兩 tab 自帶多套 config 管理 + 卡片內啟用切換；vertical tab 維護分組↔CATEGORY 映射（版本化）。
// 帳號 → 獨立抽屜（topbar email chip 開）；歸因判準規則 → AI 法官主頁路由 /judge/rules（不在此）。
// 分頁狀態同步 URL query(?settings=llm|qc|vertical)，並相容舊深連結。
type SettingsTab = 'llm' | 'qc' | 'vertical';

const visible = defineModel<boolean>('visible', { default: false });
const tab = defineModel<SettingsTab>('tab', { default: 'llm' });

const route = useRoute();
const router = useRouter();

const syncQuery = (t: SettingsTab) => router.replace({ query: { ...route.query, settings: t } });
const clearQuery = () => {
  if (!route.query.settings) return;
  const q = { ...route.query };
  delete q.settings;
  router.replace({ query: q });
};

watch(visible, (v) => (v ? syncQuery(tab.value) : clearQuery()));
watch(tab, (t) => {
  if (visible.value) syncQuery(t);
});

onMounted(async () => {
  await router.isReady();
  const s = route.query.settings; // 深連結：?settings=xxx 自動開抽屜對應分頁
  if (s === 'qc' || s === 'datasource') {
    tab.value = 'qc';
    visible.value = true;
  } else if (s === 'vertical') {
    tab.value = 'vertical';
    visible.value = true;
  } else if (s === 'llm' || s === 'connections' || s === 'config' || s === 'model') {
    // 'connections' / 'config' / 'model' 為舊分頁名，重構後一律導向 'llm'（兼容舊深連結）
    tab.value = 'llm';
    visible.value = true;
  } else if (s === 'rules' || s === 'taxonomy') {
    // 規則已移出設定 → 導去 AI 法官主頁路由（兼容舊深連結）
    router.replace('/judge/rules');
  }
  // 'account' 由殼層 AccountDrawer 處理，不在此開
});
</script>

<template>
  <a-drawer
    v-model:visible="visible"
    placement="right"
    title="⚙️ 配置"
    :width="640"
    :footer="false"
    unmount-on-close
  >
    <a-tabs v-model:active-key="tab">
      <a-tab-pane key="llm" title="🤖 LLM 模型連線"><LlmConnectionsPanel /></a-tab-pane>
      <a-tab-pane key="qc" title="🗄️ QC DB 連線"><QcConnectionsPanel /></a-tab-pane>
      <a-tab-pane key="vertical" title="🧭 商品垂直分類">
        <ProductVerticalSettingsPanel :active="tab === 'vertical'" />
      </a-tab-pane>
    </a-tabs>
  </a-drawer>
</template>

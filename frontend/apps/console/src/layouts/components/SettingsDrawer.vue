<script setup lang="ts">
import { watch, onMounted } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { LlmConnectionsPanel, QcConnectionsPanel } from '@/features/settings/pages';

// ⚙️ 設定抽屜＝「公共配置」：右滑疊加，兩分頁直接是基礎連線層 —— 🤖 LLM 模型 ｜ 🗄️ QC DB 接口。
// 各 tab 自帶多套 config 管理 + 卡片內啟用切換（不再獨立「啟用」分頁）。
// 帳號 → 獨立抽屜（topbar email chip 開）；規則 → AI 法官主頁路由 /judge/rules（不在此）。
// 分頁狀態同步 URL query(?settings=llm|qc)，並相容舊深連結。
type SettingsTab = 'llm' | 'qc';

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
    title="⚙️ 設定"
    :width="640"
    :footer="false"
    unmount-on-close
  >
    <a-tabs v-model:active-key="tab">
      <a-tab-pane key="llm" title="🤖 LLM 模型"><LlmConnectionsPanel /></a-tab-pane>
      <a-tab-pane key="qc" title="🗄️ QC DB 接口"><QcConnectionsPanel /></a-tab-pane>
    </a-tabs>
  </a-drawer>
</template>

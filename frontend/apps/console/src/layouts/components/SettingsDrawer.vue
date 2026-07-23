<script setup lang="ts">
import { watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import {
  LlmConnectionsPanel,
  QcConnectionsPanel,
  DataImportPanel,
} from '@/features/settings/pages';
import { ProductVerticalSettingsPanel } from '@/features/judge/components';
import { PERM } from '@/api';
import { usePermission } from '@/composables/usePermission';

// 資料導入 tab 需 data.datapack.import 權限——現在 default 清單中（人人可用）；
// gating 接線保留，日後要收緊只改 config/global/permissions.json 的 default，前端零改。
const { can } = usePermission();

// ⚙️ 配置抽屜＝「公共配置」：右滑疊加，四分頁 —— 🤖 LLM 連線 ｜ 🗄️ QC DB 連線 ｜ 🧭 商品垂直分類 ｜ 💾 資料匯出入。
// LLM 模型旋鈕（model/thinking/reasoning/temperature）不在此分頁——已下沉各功能區就地配置＋存為區默認。
// 前兩 tab 自帶多套 config 管理 + 卡片內啟用切換；vertical tab 維護分組↔CATEGORY 映射（版本化）；
// import tab＝全庫資料包安全匯入（覆蓋式；admin 閘現階段延後）+ 導出偏好（Google Drive 資料夾，原
// 「帳號」抽屜內容，2026-07-22 帳號抽屜整個退役後併入——與「導出資料包」按鈕消費同一份偏好，放
// 一起最直覺，見 DataImportPanel.vue）。
// 歸因判準規則 → AI 法官主頁路由 /judge/rules（不在此）。
// 分頁狀態同步 URL query(?settings=llm|qc|vertical|import)，並相容舊深連結。
type SettingsTab = 'llm' | 'qc' | 'vertical' | 'import';

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

// 不只處理首次載入：頁面內的「管理連線」會在既有 route 上追加 query，必須即時開抽屜。
watch(
  () => route.query.settings,
  (s) => {
    if (s === 'qc' || s === 'datasource') {
      tab.value = 'qc';
      visible.value = true;
    } else if (s === 'vertical') {
      tab.value = 'vertical';
      visible.value = true;
    } else if (
      (s === 'import' || s === 'export' || s === 'account') &&
      can(PERM.dataDatapackImport)
    ) {
      // 'export'／'account' 為舊「導出偏好」tab／帳號抽屜深連結（皆已併入 import tab，見上方註解）
      tab.value = 'import';
      visible.value = true;
    } else if (s === 'llm' || s === 'connections' || s === 'config' || s === 'model') {
      // 'connections' / 'config' / 'model' 為舊分頁名，重構後一律導向 'llm'（兼容舊深連結）
      tab.value = 'llm';
      visible.value = true;
    } else if (s === 'rules' || s === 'taxonomy') {
      // 規則已移出設定 → 導去 AI 法官主頁路由（兼容舊深連結）
      router.replace('/judge/rules');
    }
  },
  { immediate: true },
);
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
      <a-tab-pane key="llm" title="🤖 LLM 連線"><LlmConnectionsPanel /></a-tab-pane>
      <a-tab-pane key="qc" title="🗄️ QC DB 連線"><QcConnectionsPanel /></a-tab-pane>
      <a-tab-pane key="vertical" title="🧭 商品垂直分類">
        <ProductVerticalSettingsPanel :active="tab === 'vertical'" />
      </a-tab-pane>
      <a-tab-pane v-if="can(PERM.dataDatapackImport)" key="import" title="💾 資料匯出入">
        <DataImportPanel />
      </a-tab-pane>
    </a-tabs>
  </a-drawer>
</template>

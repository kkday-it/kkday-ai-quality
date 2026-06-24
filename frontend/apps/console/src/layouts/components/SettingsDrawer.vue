<script setup lang="ts">
import { watch, onMounted } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { Settings } from '@/features/settings/pages';
import { Account } from '@/features/auth/pages';

// ⚙️ 設定抽屜：右滑疊加，內含「帳號 / 模型配置」兩分頁；分頁狀態同步 URL query(?settings=)。
// 註：抽屜為疊加效果（背景主畫面保留），故用 query 反映分頁，而非佔用獨立子路由。
type SettingsTab = 'account' | 'model';

const visible = defineModel<boolean>('visible', { default: false });
const tab = defineModel<SettingsTab>('tab', { default: 'model' });

const route = useRoute();
const router = useRouter();

const syncQuery = (t: SettingsTab) => router.replace({ query: { ...route.query, settings: t } });
const clearQuery = () => {
  if (!route.query.settings) return;
  const q = { ...route.query };
  delete q.settings;
  router.replace({ query: q });
};

// 開啟 / 切分頁 → 同步 query；關閉 → 清除 query
watch(visible, (v) => (v ? syncQuery(tab.value) : clearQuery()));
watch(tab, (t) => {
  if (visible.value) syncQuery(t);
});

onMounted(async () => {
  await router.isReady(); // 等初次導航完成，route.query 才就緒
  const s = route.query.settings; // 深連結：URL 帶 ?settings=xxx 自動開抽屜對應分頁
  if (s === 'account' || s === 'model') {
    tab.value = s;
    visible.value = true;
  }
});
</script>

<template>
  <a-drawer v-model:visible="visible" placement="right" title="⚙️ 設定" :width="640" :footer="false" unmount-on-close>
    <a-tabs v-model:active-key="tab">
      <a-tab-pane key="account" title="👤 帳號"><Account /></a-tab-pane>
      <a-tab-pane key="model" title="⚙️ 模型配置"><Settings /></a-tab-pane>
    </a-tabs>
  </a-drawer>
</template>

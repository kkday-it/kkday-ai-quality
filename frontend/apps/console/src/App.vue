<script setup lang="ts">
import { ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';

const route = useRoute();
const router = useRouter();

// 功能模組（下拉選擇，默認第一個；目前僅 AI 法官）
const MODULES = [{ value: 'ai-judge', label: '⚖️ AI 法官' }];
const activeModule = ref(MODULES[0].value);

// AI 法官的兩個視角（橫向 tab）
const TABS = [
  { key: '/upload', label: '資料上傳' },
  { key: '/analytics', label: 'RD／品控 分析' },
  { key: '/product', label: 'PM／AM 單品' },
];
const activeTab = ref(route.path);
watch(() => route.path, (p) => (activeTab.value = p));
const onTab = (key: string | number) => router.push(String(key));
</script>

<template>
  <a-layout style="min-height: 100vh">
    <div class="topnav">
      <span class="brand">AI 商品質檢</span>
      <span class="divider">/</span>
      <a-select v-model="activeModule" class="mod-select" :bordered="false">
        <a-option v-for="m in MODULES" :key="m.value" :value="m.value">{{ m.label }}</a-option>
      </a-select>
      <a class="nav-link" :class="{ active: route.path === '/settings' }" @click="router.push('/settings')">⚙️ 設定</a>
    </div>

    <a-tabs :active-key="activeTab" type="line" class="subtabs" @change="onTab">
      <a-tab-pane v-for="t in TABS" :key="t.key" :title="t.label" />
    </a-tabs>

    <a-layout-content class="ct">
      <router-view />
    </a-layout-content>
  </a-layout>
</template>

<style>
.topnav { display: flex; align-items: center; gap: 6px; height: 52px; padding: 0 20px; background: #fff; border-bottom: 1px solid #f0f0f0; }
.brand { font-weight: 700; color: #165dff; font-size: 16px; user-select: none; }
.divider { color: #c9cdd4; }
.mod-select { width: 150px; font-weight: 600; }
.nav-link { margin-left: auto; cursor: pointer; color: #4e5969; font-size: 14px; padding: 4px 12px; border-radius: 6px; user-select: none; }
.nav-link.active { color: #165dff; background: #e8f3ff; font-weight: 600; }
.subtabs { background: #fff; padding: 0 12px; border-bottom: 1px solid #f0f0f0; }
.subtabs .arco-tabs-nav::before { display: none; }
.ct { padding: 20px; background: #f7f8fa; }
</style>

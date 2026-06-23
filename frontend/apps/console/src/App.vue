<script setup lang="ts">
import { useRoute, useRouter } from 'vue-router';

const route = useRoute();
const router = useRouter();
const PAGES: Record<string, string> = {
  '/analytics': 'RD／品控 分析',
  '/product': 'PM／AM 單品',
};
const onSelect = (v: string | number | Record<string, any>) => router.push(String(v));
</script>

<template>
  <a-layout style="min-height: 100vh">
    <div class="topnav">
      <a-dropdown trigger="hover" @select="onSelect">
        <span class="brand">AI 商品質檢 <span class="caret">▾</span></span>
        <template #content>
          <a-dsubmenu>
            <template #default>⚖️ AI 法官</template>
            <template #content>
              <a-doption value="/analytics">RD／品控 分析</a-doption>
              <a-doption value="/product">PM／AM 單品</a-doption>
            </template>
          </a-dsubmenu>
        </template>
      </a-dropdown>
      <span class="sep">/</span>
      <span class="pillar">AI 法官</span>
      <span class="sep">/</span>
      <span class="current">{{ PAGES[route.path] || '' }}</span>
    </div>
    <a-layout-content class="ct">
      <router-view />
    </a-layout-content>
  </a-layout>
</template>

<style>
.topnav { display: flex; align-items: center; gap: 10px; height: 56px; padding: 0 20px; background: #fff; border-bottom: 1px solid #eee; }
.brand { font-weight: 700; color: #165dff; font-size: 16px; cursor: pointer; user-select: none; }
.pillar { color: #4e5969; font-weight: 600; }
.caret { font-size: 12px; }
.sep { color: #c9cdd4; }
.current { color: #1d2129; font-weight: 600; }
.ct { padding: 20px; background: #f7f8fa; }
</style>

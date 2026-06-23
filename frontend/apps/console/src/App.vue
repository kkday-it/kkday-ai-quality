<script setup lang="ts">
import { useRoute, useRouter } from 'vue-router';

const route = useRoute();
const router = useRouter();
const go = (key: string) => {
  if (key.startsWith('/')) router.push(key);
};
</script>

<template>
  <a-layout style="min-height: 100vh">
    <a-menu mode="horizontal" :selected-keys="[route.path]" class="topnav" @menu-item-click="go">
      <a-menu-item key="logo" disabled class="logo-item">AI 商品質檢 · AI 法官</a-menu-item>
      <a-menu-item key="/analytics">品控分析（出口B）</a-menu-item>
      <a-menu-item key="/product">單品診斷（出口A）</a-menu-item>
    </a-menu>
    <a-layout-content class="ct">
      <router-view />
    </a-layout-content>
  </a-layout>
</template>

<style>
.topnav { background: #fff; border-bottom: 1px solid #eee; padding: 0 12px; }
/* logo 作為第一項，藍色突出、不可點、不灰化 */
.logo-item.arco-menu-item.arco-menu-item-disabled {
  color: #165dff !important;
  font-weight: 700;
  font-size: 16px;
  cursor: default;
  opacity: 1 !important;
  margin-right: 28px;
}
.ct { padding: 20px; background: #f7f8fa; }
</style>

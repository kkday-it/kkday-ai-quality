<script setup lang="ts">
import { useRoute, useRouter } from 'vue-router';

const route = useRoute();
const router = useRouter();
const go = (key: string) => router.push(key);
</script>

<template>
  <a-layout style="min-height: 100vh">
    <a-layout-header class="hd">
      <span class="logo">AI 商品質檢 · AI 法官</span>
      <a-menu mode="horizontal" :selected-keys="[route.path]" @menu-item-click="go">
        <a-menu-item key="/analytics">品控分析（出口B）</a-menu-item>
        <a-menu-item key="/product">單品診斷（出口A）</a-menu-item>
      </a-menu>
    </a-layout-header>
    <a-layout-content class="ct">
      <router-view />
    </a-layout-content>
  </a-layout>
</template>

<style>
.hd { display: flex; gap: 24px; align-items: center; background: #fff; border-bottom: 1px solid #eee; }
.logo { font-weight: 700; color: #165dff; white-space: nowrap; }
.ct { padding: 20px; background: #f7f8fa; }
</style>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { useAuthStore } from '@/stores';
import { JUDGE_TABS } from '@/features/judge/routes';
import { AppTopbar, FeatureTabs, SettingsDrawer, AccountDrawer } from './components';

// 應用殼層：固定 topbar（品牌列 + 視圖 tab）+ 內部滾動內容區 + 兩個獨立抽屜（公共設定 / 帳號）。
// 公開頁（登入/註冊）全屏直通，不套殼層。

const route = useRoute();
const router = useRouter();
const auth = useAuthStore();

const isPublic = computed(() => route.meta.public === true);

// ⚙️ 設定＝公共配置抽屜（LLM/QC）；👤 帳號＝獨立抽屜。各自開關互不干擾。
const settingsVisible = ref(false);
const accountVisible = ref(false);
const openSettings = () => (settingsVisible.value = true);
const openAccount = () => (accountVisible.value = true);

onMounted(async () => {
  auth.fetchMe(); // 以既有 token 拉當前 user（顯示 email）
  await router.isReady();
  if (route.query.settings === 'account') accountVisible.value = true; // 舊 ?settings=account 深連結 → 帳號抽屜
});
</script>

<template>
  <router-view v-if="isPublic" />
  <a-layout v-else class="flex h-screen flex-col overflow-hidden">
    <!-- 頂部菜單欄：固定不隨內容滾動 -->
    <div class="z-10 flex-none">
      <AppTopbar :user="auth.user" @open-settings="openSettings" @open-account="openAccount" />
      <FeatureTabs :tabs="JUDGE_TABS" />
      <!--
        頁面級工具列插槽：頁面以 <Teleport to="#page-toolbar"> 把自己的全局工具列（如歸因總覽的
        篩選列）送進這條固定 header，使其恆常可見且絕不與內容區的 ECharts canvas 重疊。
        無頁面注入時為空 div（0 高），不影響其餘 tab。
      -->
      <div id="page-toolbar"></div>
    </div>

    <!-- 內容區：內部滾動 -->
    <div class="min-h-0 flex-1 overflow-y-auto bg-[#f7f8fa] p-5">
      <router-view />
    </div>

    <SettingsDrawer v-model:visible="settingsVisible" />
    <AccountDrawer v-model:visible="accountVisible" />
  </a-layout>
</template>

<script setup lang="ts">
import { onMounted } from 'vue';
import { AUTH_PROVIDER, BE2_CONFIG } from '@/api';

// be2 SSO 模式（authProvider=be2）：本頁不出帳密表單，整頁跳轉 Auth Service 登入
// （對齊 be2 系慣例 window.location.replace('/v2/auth/login')；URL 待 platform 註冊回填）。
// 本地模式無登入系統（router 守衛已擋 /login 導回首頁，正常不會渲染到本頁）；
// 若仍被直接訪問（如舊書籤），顯示提示而非空白頁。
onMounted(() => {
  if (AUTH_PROVIDER === 'be2' && BE2_CONFIG.be2LoginUrl && !BE2_CONFIG.be2LoginUrl.includes('REPLACE_ME')) {
    window.location.replace(BE2_CONFIG.be2LoginUrl);
  }
});
</script>

<template>
  <div class="flex h-screen items-center justify-center bg-[#f7f8fa]">
    <a-card class="w-[380px] text-center">
      <div v-if="AUTH_PROVIDER === 'be2'" class="text-sm text-[#4e5969]">正在導向登入頁面…</div>
      <div v-else class="text-sm text-[#4e5969]">本地模式無需登入，請由選單返回主控台。</div>
    </a-card>
  </div>
</template>

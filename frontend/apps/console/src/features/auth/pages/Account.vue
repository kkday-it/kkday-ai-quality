<script setup lang="ts">
// 帳號管理（設定抽屜內分頁）：顯示當前登入者 + 登出 / 切換帳號。
// 首次登入仍走全屏 Login.vue（未登入關卡）；此頁為已登入後的帳號管理。
import { useRouter } from 'vue-router';
import { Message } from '@arco-design/web-vue';
import { useAuthStore } from '@/stores';

const router = useRouter();
const auth = useAuthStore();

const onLogout = () => {
  auth.logout();
  Message.success('已登出');
  router.push('/login');
};
</script>

<template>
  <div class="flex flex-col gap-5">
    <div>
      <div class="text-xs text-[#86909c]">登入帳號</div>
      <div class="text-base font-semibold text-[#1d2129]">{{ auth.user?.email || '—' }}</div>
    </div>
    <div v-if="auth.user?.created_at">
      <div class="text-xs text-[#86909c]">建立時間</div>
      <div class="text-sm text-[#4e5969]">{{ auth.user.created_at }}</div>
    </div>
    <a-button status="danger" long @click="onLogout">登出 / 切換帳號</a-button>
    <div class="text-xs text-[#86909c] leading-relaxed">
      登出後將回到登入頁，可用其他帳號登入。每個帳號的 LLM 設定（key / model / 清單）互相隔離。
    </div>
  </div>
</template>

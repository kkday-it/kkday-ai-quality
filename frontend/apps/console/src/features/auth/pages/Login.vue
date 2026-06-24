<script setup lang="ts">
import { ref } from 'vue';
import { useRouter } from 'vue-router';
import { Message } from '@arco-design/web-vue';
import { useAuthStore } from '@/stores';

const router = useRouter();
const auth = useAuthStore();

const mode = ref<'login' | 'register'>('login');
const email = ref('');
const password = ref('');
const submitting = ref(false);

const submit = async () => {
  if (!email.value.trim() || !password.value) {
    Message.warning('請輸入 email 與密碼');
    return;
  }
  submitting.value = true;
  try {
    if (mode.value === 'login') await auth.login(email.value.trim(), password.value);
    else await auth.register(email.value.trim(), password.value);
    Message.success(mode.value === 'login' ? '登入成功' : '註冊成功');
    router.push('/');
  } catch (e: any) {
    Message.error(e?.message || '操作失敗');
  } finally {
    submitting.value = false;
  }
};
</script>

<template>
  <div class="flex h-screen items-center justify-center bg-[#f7f8fa]">
    <a-card class="w-[380px]">
      <div class="text-lg font-bold text-[#165dff]">⚖️ AI 商品質檢 · AI 法官</div>
      <div class="mb-[18px] mt-1 text-[13px] text-[#86909c]">{{ mode === 'login' ? '登入以繼續' : '註冊新帳號' }}</div>
      <a-form :model="{ email, password }" layout="vertical" @submit.prevent>
        <a-form-item label="Email">
          <a-input v-model="email" placeholder="you@example.com" allow-clear @keyup.enter="submit" />
        </a-form-item>
        <a-form-item label="密碼（至少 6 碼）">
          <a-input-password v-model="password" placeholder="密碼" allow-clear @keyup.enter="submit" />
        </a-form-item>
        <a-button type="primary" long :loading="submitting" @click="submit">
          {{ mode === 'login' ? '登入' : '註冊' }}
        </a-button>
      </a-form>
      <div class="mt-3.5 text-center text-[13px] text-[#86909c]">
        <span v-if="mode === 'login'">還沒有帳號？<a class="cursor-pointer text-[#165dff]" @click="mode = 'register'">註冊</a></span>
        <span v-else>已有帳號？<a class="cursor-pointer text-[#165dff]" @click="mode = 'login'">登入</a></span>
      </div>
    </a-card>
  </div>
</template>

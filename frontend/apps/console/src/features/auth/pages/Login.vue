<script setup lang="ts">
import { useAuthStore } from '@/stores';
import { Message, type FormInstance } from '@arco-design/web-vue';
import { reactive, ref } from 'vue';
import { useRouter } from 'vue-router';

const router = useRouter();
const auth = useAuthStore();

const mode = ref<'login' | 'register'>('login');
// 穩定 reactive model（取代 :model={email,password} 字面量，Arco 才追得到欄位路徑）
const form = reactive({ email: '', password: '' });
const formRef = ref<FormInstance>();
const rules = {
  email: [{ required: true, message: '請輸入 Email' }],
  password: [
    { required: true, message: '請輸入密碼' },
    { minLength: 6, message: '密碼至少 6 碼' },
  ],
};
const submitting = ref(false);

const submit = async () => {
  if (await formRef.value?.validate()) return; // 有錯 → 行內顯示、不送出
  submitting.value = true;
  try {
    if (mode.value === 'login') await auth.login(form.email.trim(), form.password);
    else await auth.register(form.email.trim(), form.password);
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
      <div class="text-lg font-bold text-[#165dff]">⚖️ AI 質檢</div>
      <div class="mb-[18px] mt-1 text-[13px] text-[#86909c]">
        {{ mode === 'login' ? '登入以繼續' : '註冊新帳號' }}
      </div>
      <a-form ref="formRef" :model="form" :rules="rules" layout="vertical" @submit.prevent>
        <a-form-item field="email" label="Email">
          <a-input
            v-model="form.email"
            placeholder="you@example.com"
            allow-clear
            @keyup.enter="submit"
          />
        </a-form-item>
        <a-form-item field="password" label="密碼（至少 6 碼）">
          <a-input-password
            v-model="form.password"
            placeholder="密碼"
            allow-clear
            @keyup.enter="submit"
          />
        </a-form-item>
        <a-button type="primary" long :loading="submitting" @click="submit">
          {{ mode === 'login' ? '登入' : '註冊' }}
        </a-button>
      </a-form>
      <div class="mt-3.5 text-center text-[13px] text-[#86909c]">
        <span v-if="mode === 'login'"
          >還沒有帳號？<a class="cursor-pointer text-[#165dff]" @click="mode = 'register'"
            >註冊</a
          ></span
        >
        <span v-else
          >已有帳號？<a class="cursor-pointer text-[#165dff]" @click="mode = 'login'">登入</a></span
        >
      </div>
    </a-card>
  </div>
</template>

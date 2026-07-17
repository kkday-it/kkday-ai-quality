<script setup lang="ts">
import { useAuthStore } from '@/stores';
import { translateApiError } from '@/i18n';
import { Message, type FormInstance } from '@arco-design/web-vue';
import { reactive, ref } from 'vue';
import { useRouter } from 'vue-router';
import { useI18n } from 'vue-i18n';

const { t } = useI18n();
const router = useRouter();
const auth = useAuthStore();

const mode = ref<'login' | 'register'>('login');
// 穩定 reactive model（取代 :model={email,password} 字面量，Arco 才追得到欄位路徑）
const form = reactive({ email: '', password: '' });
const formRef = ref<FormInstance>();
const rules = {
  email: [{ required: true, message: t('auth.login.emailRequired') }],
  password: [
    { required: true, message: t('auth.login.passwordRequired') },
    { minLength: 6, message: t('auth.login.passwordMin') },
  ],
};
const submitting = ref(false);

const submit = async () => {
  if (await formRef.value?.validate()) return; // 有錯 → 行內顯示、不送出
  submitting.value = true;
  try {
    if (mode.value === 'login') await auth.login(form.email.trim(), form.password);
    else await auth.register(form.email.trim(), form.password);
    Message.success(
      mode.value === 'login' ? t('auth.login.successLogin') : t('auth.login.successRegister'),
    );
    router.push('/');
  } catch (e) {
    Message.error(translateApiError(e) || t('auth.login.failFallback'));
  } finally {
    submitting.value = false;
  }
};
</script>

<template>
  <div class="flex h-screen items-center justify-center bg-[#f7f8fa]">
    <a-card class="w-[380px]">
      <div class="text-lg font-bold text-[#165dff]">{{ t('common.app.name') }}</div>
      <div class="mb-[18px] mt-1 text-[13px] text-[#86909c]">
        {{ mode === 'login' ? t('auth.login.subtitleLogin') : t('auth.login.subtitleRegister') }}
      </div>
      <a-form ref="formRef" :model="form" :rules="rules" layout="vertical" @submit.prevent>
        <a-form-item field="email" :label="t('auth.login.emailLabel')">
          <a-input
            v-model="form.email"
            :placeholder="t('auth.login.emailPlaceholder')"
            allow-clear
            @keyup.enter="submit"
          />
        </a-form-item>
        <a-form-item field="password" :label="t('auth.login.passwordLabel')">
          <a-input-password
            v-model="form.password"
            :placeholder="t('auth.login.passwordPlaceholder')"
            allow-clear
            @keyup.enter="submit"
          />
        </a-form-item>
        <a-button type="primary" long :loading="submitting" @click="submit">
          {{ mode === 'login' ? t('auth.login.submitLogin') : t('auth.login.submitRegister') }}
        </a-button>
      </a-form>
      <div class="mt-3.5 text-center text-[13px] text-[#86909c]">
        <span v-if="mode === 'login'"
          >{{ t('auth.login.noAccount')
          }}<a class="cursor-pointer text-[#165dff]" @click="mode = 'register'">{{
            t('auth.login.toRegister')
          }}</a></span
        >
        <span v-else
          >{{ t('auth.login.hasAccount')
          }}<a class="cursor-pointer text-[#165dff]" @click="mode = 'login'">{{
            t('auth.login.toLogin')
          }}</a></span
        >
      </div>
    </a-card>
  </div>
</template>

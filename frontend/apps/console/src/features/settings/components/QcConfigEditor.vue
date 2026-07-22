<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue';
import { Message, type FormInstance } from '@arco-design/web-vue';
import qcDefaults from '@config/global/qc_db.json';
import { testQcDb } from '@/api';
import type { QcDbTestResult } from '@/api';
import { Terminal } from '@/components';
import { configStamp } from '../utils';
import type { QcConfig } from '../types';

// 單套 QC DB config 編輯器：props 注入 config + 已知 password，emit save 由父元件持久化。
// 環境完全隔離：連線所屬環境由父層環境 tab 決定（新增即繼承、建立後不可變），
// 編輯器不提供環境選擇——跨環境殘值從結構上不存在；host 預設值仍按所屬環境帶入。
const QC = qcDefaults;
const ENVS = QC.environments;
const DEFAULT_ENV = QC.defaultEnv;
const envOf = (id: string) =>
  ENVS.find((e) => e.id === id) ?? ENVS.find((e) => e.id === DEFAULT_ENV) ?? ENVS[0];

const props = defineProps<{
  /** 編輯中的 config（新建時由父元件帶 id + 預設值）。 */
  modelValue: QcConfig;
  /** 此 config 已知明文 password（供眼睛切換 / 留空不變更）。 */
  password: string;
}>();
const emit = defineEmits<{
  (e: 'save', payload: { config: QcConfig; password?: string }): void;
  (e: 'cancel'): void;
}>();

const form = ref({
  label: props.modelValue.label,
  env: props.modelValue.env || DEFAULT_ENV,
  host: props.modelValue.host || envOf(props.modelValue.env || DEFAULT_ENV).host,
  port: props.modelValue.port ?? (QC.port as number),
  user: props.modelValue.user,
  password: props.password,
});
const pwDirty = ref(false);
const saving = ref(false);
const testing = ref(false);
const hasPassword = computed(() => !!(props.password || (pwDirty.value && form.value.password)));

/** 組出當前表單的 QcConfig（保留 id）。 */
const buildConfig = (): QcConfig => ({
  id: props.modelValue.id,
  label: form.value.label.trim() || `QC DB ${configStamp()}`,
  env: form.value.env,
  host: form.value.host,
  port: form.value.port,
  user: form.value.user,
});

// Arco 宣告式驗證：required 欄走 rules + formRef.validate()（取代散落的手寫 Message.warning）
const formRef = ref<FormInstance>();
const rules = {
  host: [{ required: true, message: '請輸入 Host' }],
  user: [{ required: true, message: '請輸入 User' }],
};

const onSave = async () => {
  if (await formRef.value?.validate()) return; // 有錯 → 行內顯示、不送出
  saving.value = true;
  emit('save', {
    config: buildConfig(),
    password: pwDirty.value && form.value.password ? form.value.password : undefined,
  });
  saving.value = false;
};

// ── 即時連通性測試（password 空/遮罩後端反查 qc_passwords[config_id]）──
type QcDbTestView = QcDbTestResult & { target?: string };
const testResult = ref<QcDbTestView | null>(null);
const termRef = ref<InstanceType<typeof Terminal>>();
const onTest = async () => {
  testing.value = true;
  testResult.value = null;
  try {
    const r = await testQcDb({
      config_id: props.modelValue.id,
      env: form.value.env,
      host: form.value.host,
      port: form.value.port,
      user: form.value.user,
      password: pwDirty.value && form.value.password ? form.value.password : undefined,
    });
    testResult.value = { ...r, target: `${form.value.host}:${form.value.port}` };
    if (r.ok) {
      Message.success('連線成功');
    } else {
      Message.error('連線失敗：' + (r.error || '未知錯誤'));
    }
  } catch (e: any) {
    testResult.value = { ok: false, error: e?.message || String(e) };
    Message.error('測試失敗：' + (e?.message || e));
  } finally {
    testing.value = false;
  }
};

const ANSI = {
  reset: '\x1b[0m',
  green: '\x1b[32m',
  red: '\x1b[31m',
  dim: '\x1b[90m',
} as const;
watch(testResult, async (r) => {
  if (!r) return;
  await nextTick();
  const t = termRef.value;
  if (!t) return;
  t.clear();
  t.writeln(r.ok ? `${ANSI.green}● 連線成功${ANSI.reset}` : `${ANSI.red}● 連線失敗${ANSI.reset}`);
  if (r.target) t.writeln(`${ANSI.dim}# ${r.target}${ANSI.reset}`);
  if (r.error) t.writeln(`${ANSI.red}✗ ${r.error}${ANSI.reset}`);
});
</script>

<template>
  <a-form ref="formRef" :model="form" :rules="rules" layout="vertical">
    <a-form-item field="label" label="連線名稱">
      <a-input v-model="form.label" placeholder="可自訂名稱（預設 qc 環境 db + 時間戳）" allow-clear />
    </a-form-item>

    <a-row :gutter="12">
      <a-col :span="16">
        <a-form-item field="host" label="Host">
          <a-input v-model="form.host" :placeholder="envOf(form.env).host" allow-clear />
        </a-form-item>
      </a-col>
      <a-col :span="8">
        <a-form-item field="port" label="Port">
          <a-input-number
            v-model="form.port"
            :min="1"
            :max="65535"
            :placeholder="String(QC.port)"
            class="w-full"
          />
        </a-form-item>
      </a-col>
    </a-row>

    <a-row :gutter="12">
      <a-col :span="12">
        <a-form-item field="user" label="User">
          <a-input v-model="form.user" placeholder="資料庫帳號" allow-clear />
        </a-form-item>
      </a-col>
      <a-col :span="12">
        <a-form-item field="password" label="Password">
          <a-input-password
            v-model="form.password"
            :placeholder="hasPassword ? '已設定（留空不變更）' : '請輸入密碼'"
            allow-clear
            @input="pwDirty = true"
          />
        </a-form-item>
      </a-col>
    </a-row>

    <a-space align="center" :size="8">
      <a-button type="primary" status="success" :loading="testing" @click="onTest"
        >測試連線</a-button
      >
      <a-button type="primary" :loading="saving" @click="onSave"> 儲存 </a-button>
      <a-button @click="emit('cancel')">取消</a-button>
      <span class="text-xs text-[#86909c]">測試＝即時測當前表單連通性（不寫入）；儲存＝寫入此套連線</span>
    </a-space>

    <Terminal v-if="testResult" ref="termRef" class="mt-3" height="6rem" />
  </a-form>
</template>

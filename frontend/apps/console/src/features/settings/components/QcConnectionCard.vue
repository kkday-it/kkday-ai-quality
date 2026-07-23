<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue';
import { Message } from '@arco-design/web-vue';
import qcDefaults from '@config/global/qc_db.json';
import { testQcDb } from '@/api';
import type { QcDbTestResult } from '@/api';
import { Terminal } from '@/components';
import type { QcConnection } from '../types';

// 單一環境 QC DB 連線卡：每環境（sit/stage/production）固定恰一條連線，
// 無新增/刪除/啟用/排序——環境本身即 key，結構上不存在跨環境殘值。
const QC = qcDefaults;
const ENVS = QC.environments;
const envOf = (id: string) => ENVS.find((e) => e.id === id) ?? ENVS[0];

const props = defineProps<{
  env: string;
  connection: QcConnection | undefined;
  /** 本 session 已知明文 password（供眼睛切換）。 */
  passwordKnown: string;
  hasPassword: boolean;
  /** 是否可編輯/測試（settings.qc-config.manage）；false 時唯讀顯示狀態點。 */
  canManage: boolean;
}>();
const emit = defineEmits<{
  (e: 'save', payload: { conn: QcConnection; password?: string }): void;
}>();

const envMeta = computed(() => envOf(props.env));
const form = ref({
  host: props.connection?.host || envMeta.value.host,
  port: props.connection?.port ?? (QC.port as number),
  user: props.connection?.user ?? '',
  password: props.passwordKnown ?? '',
});
watch(
  () => props.connection,
  (c) => {
    form.value.host = c?.host || envMeta.value.host;
    form.value.port = c?.port ?? (QC.port as number);
    form.value.user = c?.user ?? '';
  },
);
const pwDirty = ref(false);
// loadSecrets() 是 parent onMounted 才觸發的非同步請求，一定晚於本元件 setup 完成——
// passwordKnown 剛掛載時必是空字串，需這個 watch 補上遲到的明文回填（未手動編輯時才覆蓋，避免蓋掉輸入中的值）。
watch(
  () => props.passwordKnown,
  (v) => {
    if (!pwDirty.value) form.value.password = v ?? '';
  },
);
const saving = ref(false);
const testing = ref(false);
const hasPasswordDisplay = computed(() => props.hasPassword || (pwDirty.value && !!form.value.password));

const onSave = async () => {
  saving.value = true;
  try {
    emit('save', {
      conn: { host: form.value.host, port: form.value.port, user: form.value.user },
      password: pwDirty.value && form.value.password ? form.value.password : undefined,
    });
    Message.success('已儲存連線');
  } finally {
    saving.value = false;
  }
};

// ── 即時連通性測試（password 空/遮罩後端反查 qc_passwords[env]）──
type QcDbTestView = QcDbTestResult & { target?: string };
const testResult = ref<QcDbTestView | null>(null);
const termRef = ref<InstanceType<typeof Terminal>>();
const onTest = async () => {
  testing.value = true;
  testResult.value = null;
  try {
    const r = await testQcDb({
      env: props.env,
      host: form.value.host,
      port: form.value.port,
      user: form.value.user,
      password: pwDirty.value && form.value.password ? form.value.password : undefined,
    });
    testResult.value = { ...r, target: `${form.value.host}:${form.value.port}` };
    if (r.ok) Message.success('連線成功');
    else Message.error('連線失敗：' + (r.error || '未知錯誤'));
  } catch (e: any) {
    testResult.value = { ok: false, error: e?.message || String(e) };
    Message.error('測試失敗：' + (e?.message || e));
  } finally {
    testing.value = false;
  }
};

const ANSI = { reset: '\x1b[0m', green: '\x1b[32m', red: '\x1b[31m', dim: '\x1b[90m' } as const;
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
  <a-card :bordered="true" size="small" class="mb-3">
    <template #title>
      <span class="inline-flex items-center gap-1.5">
        {{ envMeta.label }}
        <span
          class="inline-block h-1.5 w-1.5 rounded-full"
          :class="hasPasswordDisplay ? 'bg-[rgb(var(--green-6))]' : 'bg-[rgb(var(--gray-4))]'"
        />
      </span>
    </template>
    <a-form :model="form" layout="vertical">
      <a-row :gutter="12">
        <a-col :span="12">
          <a-form-item field="host" label="Host">
            <a-input v-model="form.host" :disabled="!canManage" :placeholder="envMeta.host" allow-clear />
          </a-form-item>
        </a-col>
        <a-col :span="12">
          <a-form-item field="port" label="Port">
            <a-input-number
              v-model="form.port"
              :disabled="!canManage"
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
            <a-input v-model="form.user" :disabled="!canManage" placeholder="資料庫帳號" allow-clear />
          </a-form-item>
        </a-col>
        <a-col :span="12">
          <a-form-item field="password" label="Password">
            <a-input-password
              v-model="form.password"
              :disabled="!canManage"
              :placeholder="hasPasswordDisplay ? '已設定（留空不變更）' : '請輸入密碼'"
              allow-clear
              @input="pwDirty = true"
            />
          </a-form-item>
        </a-col>
      </a-row>

      <a-space v-if="canManage" align="center" :size="8">
        <a-button type="primary" status="success" :loading="testing" @click="onTest">測試連線</a-button>
        <a-button type="primary" :loading="saving" @click="onSave">儲存</a-button>
        <span class="text-xs text-[#86909c]">此環境唯一一條連線</span>
      </a-space>

      <Terminal v-if="testResult" ref="termRef" class="mt-3" height="6rem" />
    </a-form>
  </a-card>
</template>

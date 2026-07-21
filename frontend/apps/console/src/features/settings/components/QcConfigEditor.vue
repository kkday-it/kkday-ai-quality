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
  names: [...props.modelValue.names],
  schemas: props.modelValue.schemas.length ? [...props.modelValue.schemas] : [QC.schema],
  password: props.password,
});
/** 連線成功後動態載入的 database / schema 清單；測試前為空。 */
const dbOptions = ref<string[]>([]);
const schemaOptions = ref<string[]>([]);
/** 綁定區是否揭露（測試成功 / 已有儲存綁定）；獨立旗標避免清空選取時整塊閃退。 */
const bindingUnlocked = ref(props.modelValue.names.length > 0);
const pwDirty = ref(false);
const saving = ref(false);
const testing = ref(false);
const hasPassword = computed(() => !!(props.password || (pwDirty.value && form.value.password)));

const dbSelectOptions = computed(() =>
  [...new Set([...dbOptions.value, ...form.value.names])].map((d) => ({ label: d, value: d })),
);
const schemaSelectOptions = computed(() =>
  [...new Set([...schemaOptions.value, ...form.value.schemas])].map((x) => ({
    label: x,
    value: x,
  })),
);

/** 組出當前表單的 QcConfig（保留 id）。 */
const buildConfig = (): QcConfig => ({
  id: props.modelValue.id,
  label: form.value.label.trim() || `QC DB ${configStamp()}`,
  env: form.value.env,
  host: form.value.host,
  port: form.value.port,
  user: form.value.user,
  names: form.value.names,
  schemas: form.value.schemas,
});

// Arco 宣告式驗證：required 欄走 rules + formRef.validate()（取代散落的手寫 Message.warning）
const formRef = ref<FormInstance>();
const rules = {
  host: [{ required: true, message: '請輸入 Host' }],
  user: [{ required: true, message: '請輸入 User' }],
};

const onSave = async () => {
  if (await formRef.value?.validate()) return; // 有錯 → 行內顯示、不送出
  if (!form.value.names.length) {
    Message.warning('請先「測試連線」並選擇至少一個資料庫');
    return;
  }
  saving.value = true;
  emit('save', {
    config: buildConfig(),
    password: pwDirty.value && form.value.password ? form.value.password : undefined,
  });
  saving.value = false;
};

// ── 即時測試 + 列舉 db/schema（password 空/遮罩後端反查 qc_passwords[config_id]）──
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
      names: form.value.names,
      schemas: form.value.schemas,
      password: pwDirty.value && form.value.password ? form.value.password : undefined,
    });
    testResult.value = { ...r, target: `${form.value.host}:${form.value.port}` };
    if (r.ok) {
      dbOptions.value = r.databases ?? [];
      schemaOptions.value = r.schemas ?? [];
      bindingUnlocked.value = true;
      Message.success(
        `連線成功，載入 ${dbOptions.value.length} 個資料庫、${schemaOptions.value.length} 個 schema`,
      );
      // 綁定庫健檢：已綁定但伺服器上已不存在的庫（如 QC 實例輪替下線）→ 提示改綁，不靜默
      if (r.stale_names?.length) {
        Message.warning(`已綁定的庫不存在：${r.stale_names.join('、')}（請重新勾選後儲存）`);
      }
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
  yellow: '\x1b[33m',
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
  if (r.ok && r.databases)
    t.writeln(
      `${ANSI.dim}# 共 ${r.databases.length} 個資料庫、${r.schemas?.length ?? 0} 個 schema 可選${ANSI.reset}`,
    );
  if (r.ok && r.stale_names?.length)
    t.writeln(`${ANSI.yellow}⚠ 綁定庫已不存在：${r.stale_names.join('、')}（請重新勾選）${ANSI.reset}`);
  if (r.error) t.writeln(`${ANSI.red}✗ ${r.error}${ANSI.reset}`);
});
</script>

<template>
  <a-form ref="formRef" :model="form" :rules="rules" layout="vertical">
    <a-form-item field="label" label="連線名稱">
      <a-input v-model="form.label" placeholder="可自訂名稱（預設 QC DB + 時間戳）" allow-clear />
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

    <!-- 綁定資料庫（漸進式揭露——測試連線成功 / 已有儲存綁定才顯示）-->
    <template v-if="bindingUnlocked">
      <a-divider orientation="left" class="!mb-3 !mt-2">
        <span class="text-[13px] text-[#86909c]">綁定資料庫</span>
      </a-divider>
      <a-alert v-if="dbOptions.length" type="success" class="!mb-3">
        已連線，共 {{ dbOptions.length }} 個資料庫、{{ schemaOptions.length }} 個 schema
        可選——勾選後按「儲存」
      </a-alert>
      <a-alert v-else type="info" class="!mb-3">
        清單需重新載入：請按「測試連線」抓取此環境的可選資料庫 / schema（下方為既有選取）
      </a-alert>
      <a-row :gutter="12">
        <a-col :span="12">
          <a-form-item field="names" label="Database（可多選）">
            <a-select
              v-model="form.names"
              multiple
              allow-clear
              allow-search
              :options="dbSelectOptions"
              :virtual-list-props="{ height: 200 }"
              placeholder="搜尋並勾選一或多個資料庫"
            />
          </a-form-item>
        </a-col>
        <a-col :span="12">
          <a-form-item field="schemas" label="Schema（可多選）">
            <a-select
              v-model="form.schemas"
              multiple
              allow-clear
              allow-search
              :options="schemaSelectOptions"
              :virtual-list-props="{ height: 200 }"
              placeholder="搜尋並勾選一或多個 schema"
            />
          </a-form-item>
        </a-col>
      </a-row>
    </template>

    <a-space align="center" :size="8">
      <a-button type="primary" status="success" :loading="testing" @click="onTest"
        >測試連線</a-button
      >
      <a-button type="primary" :loading="saving" :disabled="!form.names.length" @click="onSave">
        儲存
      </a-button>
      <a-button @click="emit('cancel')">取消</a-button>
      <span class="text-xs text-[#86909c]">測試＝即時測當前表單（不寫入）；儲存＝寫入此套連線</span>
    </a-space>

    <Terminal v-if="testResult" ref="termRef" class="mt-3" height="6rem" />
  </a-form>
</template>

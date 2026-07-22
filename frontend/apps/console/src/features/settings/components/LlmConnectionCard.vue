<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue';
import { Message, type FormInstance } from '@arco-design/web-vue';
import { testLlm, type LlmPingResult } from '@/api';
import { Terminal } from '@/components';
import { PROVIDERS } from '../constants';
import type { LlmConnection } from '../types';

// 單一供應商連線卡：只管 base_url + token（旋鈕已下沉各功能區的 LlmKnobs/「存為此區默認」）。
// 每供應商固定恰一條連線，無新增/刪除/啟用/排序——供應商本身即 key，結構上不存在跨供應商殘值。
const props = defineProps<{
  provider: string;
  connection: LlmConnection | undefined;
  /** 本 session 已知明文 token（供眼睛切換顯示全文）。 */
  tokenKnown: string;
  /** 是否已配 token（不明文，遮罩態初始顯示用）。 */
  hasToken: boolean;
  /** 是否可編輯/測試（settings.llm-config.manage）；false 時唯讀顯示狀態點。 */
  canManage: boolean;
}>();
const emit = defineEmits<{
  (e: 'save', payload: { baseUrl: string; token?: string }): void;
}>();

const providerMeta = computed(() => PROVIDERS.find((p) => p.id === props.provider));

const form = ref({
  base_url: props.connection?.base_url ?? '',
  api_token: props.tokenKnown ?? '',
});
watch(
  () => props.connection,
  (c) => {
    form.value.base_url = c?.base_url ?? '';
  },
);
const tokenDirty = ref(false);
const saving = ref(false);
const testing = ref(false);
const hasTokenDisplay = computed(() => props.hasToken || (tokenDirty.value && !!form.value.api_token));

const formRef = ref<FormInstance>();

const onSave = async () => {
  saving.value = true;
  try {
    emit('save', {
      baseUrl: form.value.base_url,
      token: tokenDirty.value && form.value.api_token ? form.value.api_token : undefined,
    });
    Message.success('已儲存連線');
  } finally {
    saving.value = false;
  }
};

// ── 即時測試（用當前表單值 + 該供應商預設 model 做基本連通性測試；不寫入）──
const testResult = ref<LlmPingResult | null>(null);
const termRef = ref<InstanceType<typeof Terminal>>();
const onTest = async () => {
  testing.value = true;
  testResult.value = null;
  try {
    const r = await testLlm({
      provider: props.provider,
      base_url: form.value.base_url,
      model: providerMeta.value?.defaultModel ?? providerMeta.value?.defaultModels?.[0]?.id ?? '',
      thinking: 'default',
      reasoning_effort: 'default',
      api_token: tokenDirty.value && form.value.api_token ? form.value.api_token : undefined,
    });
    testResult.value = r;
    if (r.ok) Message.success('連線成功');
    else Message.error('連線失敗：' + (r.error || '未知錯誤'));
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
  cyan: '\x1b[36m',
  magenta: '\x1b[35m',
  dim: '\x1b[90m',
} as const;
watch(testResult, async (r) => {
  if (!r) return;
  await nextTick();
  const t = termRef.value;
  if (!t) return;
  t.clear();
  const head = r.ok ? `${ANSI.green}● 連線成功` : `${ANSI.red}● 連線失敗`;
  const lat = r.latency_ms ? ` ${ANSI.dim}· ${r.latency_ms}ms` : '';
  t.writeln(`${head}${lat}${ANSI.reset}`);
  t.writeln(`${ANSI.dim}# ${r.model ?? ''} @ ${r.base_url ?? ''}${ANSI.reset}`);
  if (r.sent) t.writeln(`${ANSI.green}➜${ANSI.reset} ${ANSI.cyan}send${ANSI.reset} ${r.sent}`);
  if (r.reply) t.writeln(`${ANSI.magenta}←${ANSI.reset} ${r.reply}`);
  if (r.error) t.writeln(`${ANSI.red}✗ ${r.error}${ANSI.reset}`);
});
</script>

<template>
  <a-card :bordered="true" size="small" class="mb-3">
    <template #title>
      <span class="inline-flex items-center gap-1.5">
        {{ providerMeta?.label ?? provider }}
        <span
          class="inline-block h-1.5 w-1.5 rounded-full"
          :class="hasTokenDisplay ? 'bg-[rgb(var(--green-6))]' : 'bg-[rgb(var(--gray-4))]'"
        />
      </span>
    </template>
    <a-form ref="formRef" :model="form" layout="vertical">
      <a-row :gutter="12">
        <a-col :span="12">
          <a-form-item field="api_token" label="API Token">
            <a-input-password
              v-model="form.api_token"
              :disabled="!canManage"
              :placeholder="hasTokenDisplay ? '已設定（留空不變更）' : '請輸入 token'"
              allow-clear
              @input="tokenDirty = true"
            />
          </a-form-item>
        </a-col>
        <a-col :span="12">
          <a-form-item field="base_url" label="Base URL">
            <a-input
              v-model="form.base_url"
              :disabled="!canManage"
              :placeholder="providerMeta?.base_url || '空＝供應商預設端點'"
              allow-clear
            />
          </a-form-item>
        </a-col>
      </a-row>

      <a-space v-if="canManage" align="center" :size="8">
        <a-button type="primary" status="success" :loading="testing" @click="onTest">測試連線</a-button>
        <a-button type="primary" :loading="saving" @click="onSave">儲存</a-button>
        <span class="text-xs text-[#86909c]">此供應商唯一一條連線；token 只存後端，不入 git</span>
      </a-space>

      <Terminal v-if="testResult" ref="termRef" class="mt-3" />
    </a-form>
  </a-card>
</template>

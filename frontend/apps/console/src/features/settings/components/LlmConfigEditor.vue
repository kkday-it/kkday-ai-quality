<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue';
import { isNil } from 'lodash-es';
import { Message, type FormInstance } from '@arco-design/web-vue';
import { testLlm } from '@/api';
import { Terminal } from '@/components';
import { MODEL_MIN_VERSION, PROVIDERS, REASONING } from '../constants';
import { composeLlmLabel, deriveProviderId, modelMeetsMin } from '../utils';
import type { LlmConfig } from '../types';

// 單套 LLM config 編輯器（modal 內容）：props 注入 config + 已知 token map，emit save 由父元件持久化。
// 從舊 Settings.vue 重構：保留 provider 切換 / model 動態清單 / 即時測試（Terminal）；
// 移除單一配置時代的 localStorage 快取與 per-model 旋鈕記憶（多 config 下「config 本身即真相源」）。
const props = defineProps<{
  /** 編輯中的 config（新建時由父元件帶 id + 預設值）。 */
  modelValue: LlmConfig;
  /** 此套配置自身已知明文 token（per-config，本 session 已知；供眼睛切換顯示全文）。 */
  tokenKnown: string;
}>();
const emit = defineEmits<{
  (e: 'save', payload: { config: LlmConfig; tokenPatch?: Record<string, string> }): void;
  (e: 'cancel'): void;
}>();

const form = ref({
  base_url: props.modelValue.base_url,
  model: props.modelValue.model,
  temperature: props.modelValue.temperature ?? 0,
  thinking: props.modelValue.thinking === 'on' ? 'on' : 'off',
  reasoning_effort: REASONING.includes(props.modelValue.reasoning_effort)
    ? props.modelValue.reasoning_effort
    : 'medium',
  api_token: '',
});
const selectedProvider = ref(deriveProviderId(props.modelValue.base_url));
const useTemp = ref(!isNil(props.modelValue.temperature));
/** 思考模式開啟時 temperature 鎖定為 1、不可修改（OpenAI reasoning model 僅接受預設 1，其他值回 400）。
 *  僅 OpenAI 有此限制；Gemini / ByteDance 開啟 thinking 仍可自訂 temperature，不強制鎖定。 */
const tempLocked = computed(
  () => form.value.thinking === 'on' && selectedProvider.value === 'openai',
);
// 鎖定成立即強制 temperature=1 並啟用自訂（送出 1）；immediate 讓載入既有 config／切換配置時也一併校正。
watch(
  tempLocked,
  (locked) => {
    if (locked) {
      useTemp.value = true;
      form.value.temperature = 1;
    }
  },
  { immediate: true },
);
const tokenDirty = ref(false);
const saving = ref(false);
const testing = ref(false);

// 帶入此套配置自身已知 token（明文，供眼睛切換）；hasToken 反映真實狀態（per-config）
form.value.api_token = props.tokenKnown ?? '';
const hasToken = computed(() => !!(props.tokenKnown || (tokenDirty.value && form.value.api_token)));

/** 當前供應商的 model 下拉（{id,desc}）；已選/歷史 model 不在 curated 時補一筆，再過濾版本門檻。 */
const modelOptions = computed(() => {
  const p = PROVIDERS.find((x) => x.id === selectedProvider.value);
  const curated = p?.defaultModels ?? [];
  const has = curated.some((m) => m.id === form.value.model);
  const all = form.value.model && !has ? [...curated, { id: form.value.model }] : curated;
  return all.filter((m) => modelMeetsMin(m.id, MODEL_MIN_VERSION));
});

// 切換供應商：帶入 base_url / 該 provider 已知 token / 預設 model + 旋鈕 preset
const selectProvider = (id: unknown) => {
  const p = PROVIDERS.find((x) => x.id === String(id));
  if (!p) return;
  selectedProvider.value = p.id;
  form.value.base_url = p.base_url;
  // per-config token：切換供應商不動 token（token 屬此套配置，非跟著 provider 還原）
  form.value.model = p.defaultModel ?? p.defaultModels[0]?.id ?? '';
  if (p.thinking !== undefined) form.value.thinking = p.thinking === 'on' ? 'on' : 'off';
  if (p.reasoning_effort !== undefined) form.value.reasoning_effort = p.reasoning_effort;
};

/** 組出當前表單的 LlmConfig（保留 id）。label 由參數自動拼接（provider/model/reasoning），不再手動命名。 */
const buildConfig = (): LlmConfig => ({
  id: props.modelValue.id,
  label: composeLlmLabel({
    provider: selectedProvider.value,
    model: form.value.model,
    reasoning_effort: form.value.reasoning_effort,
    thinking: form.value.thinking,
  }),
  provider: selectedProvider.value,
  base_url: form.value.base_url,
  model: form.value.model,
  temperature: useTemp.value ? form.value.temperature : null,
  thinking: form.value.thinking,
  reasoning_effort: form.value.reasoning_effort,
});

// Arco 宣告式驗證：Model 必填走 rules + formRef.validate()（取代手寫 Message.warning）
const formRef = ref<FormInstance>();
const rules = {
  model: [{ required: true, message: '請選擇或輸入 Model' }],
};

const onSave = async () => {
  if (await formRef.value?.validate()) return; // 有錯 → 行內顯示、不送出
  saving.value = true;
  // per-config token patch：key＝此套配置 id（非 provider）；dirty 且非空才帶（空/遮罩後端不覆蓋）
  const tokenPatch =
    tokenDirty.value && form.value.api_token
      ? { [props.modelValue.id]: form.value.api_token }
      : undefined;
  emit('save', { config: buildConfig(), tokenPatch });
  saving.value = false;
};

// ── 即時測試（用當前表單值；token 空/遮罩後端沿用既存）──
/** /settings/test-llm 回傳形狀（ping）。 */
interface PingResult {
  ok: boolean;
  model?: string;
  base_url?: string;
  sent?: string;
  reply?: string;
  latency_ms?: number;
  tokens?: number;
  error?: string;
  /** 後端自動降級參數時的說明（如 reasoning_effort 不被該 model 接受、已改送較低檔）。 */
  note?: string;
}
const testResult = ref<PingResult | null>(null);
const termRef = ref<InstanceType<typeof Terminal>>();
const onTest = async () => {
  testing.value = true;
  testResult.value = null;
  try {
    const r = await testLlm({
      base_url: form.value.base_url,
      model: form.value.model,
      temperature: useTemp.value ? form.value.temperature : null,
      thinking: form.value.thinking,
      reasoning_effort: form.value.reasoning_effort,
      // per-config：表單明文 token（dirty 才帶）；空/遮罩時後端沿用已儲存該套 config 的 token
      api_token: tokenDirty.value && form.value.api_token ? form.value.api_token : undefined,
      config_id: props.modelValue.id,
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
  yellow: '\x1b[33m',
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
  if (r.tokens) t.writeln(`${ANSI.dim}tokens: ${r.tokens}${ANSI.reset}`);
  if (r.note) t.writeln(`${ANSI.yellow}⚠ ${r.note}${ANSI.reset}`);
  if (r.error) t.writeln(`${ANSI.red}✗ ${r.error}${ANSI.reset}`);
});
</script>

<template>
  <a-form ref="formRef" :model="form" :rules="rules" layout="vertical">
    <a-form-item field="provider" label="供應商">
      <a-select
        :model-value="selectedProvider"
        placeholder="選擇供應商，自動帶入 base_url 與 model 清單"
        @change="selectProvider"
      >
        <a-option v-for="p in PROVIDERS" :key="p.id" :value="p.id">{{ p.label }}</a-option>
      </a-select>
    </a-form-item>

    <a-form-item field="api_token" label="API Token">
      <a-input-password
        v-model="form.api_token"
        :placeholder="hasToken ? '已設定（留空不變更）' : '請輸入 token（sk-... / Gemini key）'"
        allow-clear
        @input="tokenDirty = true"
      />
      <template #extra>
        <span class="text-xs text-[#86909c]">
          此配置專用 token（每套配置各自獨立）、只存後端（user_settings，DB），不入
          git，前端僅遮罩顯示。
        </span>
      </template>
    </a-form-item>

    <a-form-item field="model" label="Model">
      <a-select
        v-model="form.model"
        allow-create
        allow-clear
        placeholder="從預設清單選（也可手動輸入臨時 model）"
      >
        <a-option v-for="m in modelOptions" :key="m.id" :value="m.id" :label="m.id">
          <span>{{ m.id }}</span>
          <span v-if="m.desc" class="ml-2 text-xs text-[#86909c]">{{ m.desc }}</span>
        </a-option>
      </a-select>
    </a-form-item>

    <a-form-item field="base_url" label="Base URL">
      <a-input v-model="form.base_url" placeholder="空＝OpenAI 預設端點" allow-clear />
    </a-form-item>

    <a-row :gutter="12">
      <a-col :span="16">
        <a-form-item field="temperature" label="Temperature">
          <a-space>
            <a-switch v-model="useTemp" :disabled="tempLocked" />
            <span class="text-xs text-[#86909c]">{{
              tempLocked
                ? '鎖定 1（Thinking 開啟）'
                : useTemp
                  ? '自訂'
                  : 'API 預設（gpt-5 系列鎖定）'
            }}</span>
            <a-slider
              v-if="useTemp && !tempLocked"
              v-model="form.temperature"
              :min="0"
              :max="2"
              :step="0.1"
              class="w-[180px]"
            />
            <span v-if="useTemp">{{ tempLocked ? 1 : (form.temperature ?? 0) }}</span>
          </a-space>
        </a-form-item>
      </a-col>
      <a-col :span="8">
        <a-form-item field="thinking" label="思考模式 Thinking">
          <a-space>
            <a-switch v-model="form.thinking" checked-value="on" unchecked-value="off" />
            <span class="text-xs text-[#86909c]">{{
              form.thinking === 'on' ? '開啟' : '關閉'
            }}</span>
          </a-space>
        </a-form-item>
      </a-col>
    </a-row>

    <a-form-item field="reasoning_effort" label="Reasoning effort">
      <a-space>
        <a-radio-group
          v-model="form.reasoning_effort"
          type="button"
          size="small"
          :disabled="form.thinking === 'off'"
        >
          <a-radio v-for="r in REASONING" :key="r" :value="r">{{ r }}</a-radio>
        </a-radio-group>
        <span v-if="form.thinking === 'off'" class="text-xs text-[#86909c]">
          Thinking 關閉：不送推理參數（不支援完全關閉的模型自動降為最低檔）
        </span>
      </a-space>
    </a-form-item>

    <a-space align="center" :size="8">
      <a-button type="primary" status="success" :loading="testing" @click="onTest"
        >測試連線</a-button
      >
      <a-button type="primary" :loading="saving" @click="onSave">儲存</a-button>
      <a-button @click="emit('cancel')">取消</a-button>
      <span class="text-xs text-[#86909c]">測試＝即時測當前表單（不寫入）；儲存＝寫入此套配置</span>
    </a-space>

    <Terminal v-if="testResult" ref="termRef" class="mt-3" />
  </a-form>
</template>

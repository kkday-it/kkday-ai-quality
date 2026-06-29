<script setup lang="ts">
import { ref, computed, onMounted, watch, nextTick } from 'vue';
import { isNil } from 'lodash-es';
import { Message } from '@arco-design/web-vue';
import { getSettingsRaw, saveSettings, testLlm } from '@/api';
import { StateGuard, Terminal } from '@/components';
import { PROVIDERS, REASONING, DEFAULT_LLM_FORM, MODEL_MIN_VERSION } from '../constants';
import {
  modelKey,
  readOverrides,
  writeOverride,
  readSettingsCache,
  writeSettingsCache,
  deriveProviderId,
  deriveBackendProvider,
  modelMeetsMin,
} from '../utils';

// 表單初始值衍生自 config/defaults.json 的 openai preset（DEFAULT_LLM_FORM），不再寫死字面量。
const form = ref({ ...DEFAULT_LLM_FORM, api_token: '' });
const selectedProvider = ref('openai');
const hasToken = ref(false);
// 各 provider 各自一把 token（key＝provider id，與 deriveProviderId 對齊）；切換 provider 時由此還原。
// 後端 provider_tokens 單一真相源，前端僅暫存供切換還原；form.api_token 為「當前 provider 的綁定欄位」。
const providerTokens = ref<Record<string, string>>({});
const stubMode = ref(true);
const tokenDirty = ref(false);
const useTemp = ref(false);
const loading = ref(true);
const saving = ref(false);
const testing = ref(false);

// 當前供應商的 model 下拉選項（{ id, desc }）：直接讀 config/defaults.json 的 curated defaultModels。
// 當前已選/已存 model 若不在 curated（如歷史保存值 / 手動輸入）→ 補一筆（無 desc），再過濾版本門檻。
const modelOptions = computed(() => {
  const p = PROVIDERS.find((x) => x.id === selectedProvider.value);
  const curated = p?.defaultModels ?? [];
  const has = curated.some((m) => m.id === form.value.model);
  const all = form.value.model && !has ? [...curated, { id: form.value.model }] : curated;
  return all.filter((m) => modelMeetsMin(m.id, MODEL_MIN_VERSION));
});

onMounted(async () => {
  // 1) localStorage 快取（非敏感欄位）
  const cached = readSettingsCache<typeof form.value>();
  if (cached) {
    Object.assign(form.value, cached);
    useTemp.value = !isNil(cached.temperature);
  }
  // 2) 後端真值蓋上（raw 端點回明文 token，眼睛切換即可看全文）；舊值歸一到新選項集
  try {
    const s = await getSettingsRaw();
    form.value.model = s.model ?? DEFAULT_LLM_FORM.model;
    form.value.base_url = s.base_url ?? DEFAULT_LLM_FORM.base_url;
    form.value.thinking =
      s.thinking === 'on' || s.thinking === 'off' ? s.thinking : DEFAULT_LLM_FORM.thinking;
    form.value.reasoning_effort = REASONING.includes(s.reasoning_effort)
      ? s.reasoning_effort
      : DEFAULT_LLM_FORM.reasoning_effort;
    form.value.temperature = s.temperature ?? DEFAULT_LLM_FORM.temperature;
    useTemp.value = !isNil(s.temperature);
    stubMode.value = !!s.stub_mode;
    // 供應商由 base_url 反推（當前 model 已由 modelOptions union 進下拉，無需另補）
    selectedProvider.value = deriveProviderId(form.value.base_url);
    // per-provider token：raw 端點回明文 map；帶入當前 provider 的 token（眼睛切換顯示全文）
    providerTokens.value = (s.provider_tokens as Record<string, string>) ?? {};
    form.value.api_token = providerTokens.value[selectedProvider.value] ?? '';
    hasToken.value = !!form.value.api_token;
    tokenDirty.value = false;
  } catch {
    // 後端 raw 端點未就緒（如後端未重啟）時靜默降級：沿用 localStorage 快取 / default，不阻斷面板
  } finally {
    loading.value = false;
  }
});

// 切換供應商：帶入 base_url / token；model 取顯式 defaultModel（缺省回退 curated 首項）；旋鈕沿 preset + override
const selectProvider = (id: unknown) => {
  const p = PROVIDERS.find((x) => x.id === String(id));
  if (!p) return;
  selectedProvider.value = p.id;
  form.value.base_url = p.base_url;
  // 還原該 provider 已存的 token（無則空欄要求自填）；hasToken 跟著真實狀態走，不再顯示上一個 provider 的旗標
  form.value.api_token = providerTokens.value[p.id] ?? '';
  hasToken.value = !!form.value.api_token;
  tokenDirty.value = false;
  form.value.model = p.defaultModel ?? p.defaultModels[0]?.id ?? '';
  if (p.thinking !== undefined) form.value.thinking = p.thinking;
  if (p.reasoning_effort !== undefined) form.value.reasoning_effort = p.reasoning_effort;
  const ov = readOverrides()[modelKey(p.base_url, form.value.model)];
  if (ov) {
    form.value.thinking = ov.thinking;
    form.value.reasoning_effort = ov.reasoning_effort;
    useTemp.value = ov.temperature !== null;
    form.value.temperature = ov.temperature ?? 0;
  }
};

// 由當前表單組 patch（save / 即時測 共用）；token 僅在 dirty 時帶（form 已是明文，不誤送遮罩）
function buildPatch(): Record<string, unknown> {
  const patch: Record<string, unknown> = {
    provider: deriveBackendProvider(form.value.base_url),
    model: form.value.model,
    base_url: form.value.base_url,
    thinking: form.value.thinking,
    reasoning_effort: form.value.reasoning_effort,
    temperature: useTemp.value ? form.value.temperature : null,
  };
  // token 僅在 dirty 時帶，且以「當前 provider id」為 key 送 provider_tokens（後端逐 key 合併、空/遮罩不覆蓋）
  if (tokenDirty.value && form.value.api_token)
    patch.provider_tokens = { [selectedProvider.value]: form.value.api_token };
  return patch;
}

const onSave = async () => {
  saving.value = true;
  try {
    const s = await saveSettings(buildPatch());
    hasToken.value = !!s.has_token;
    stubMode.value = !!s.stub_mode;
    // 同步本地 token map：剛存的 token 記住，切走再切回免重新整理即可還原
    if (tokenDirty.value && form.value.api_token)
      providerTokens.value[selectedProvider.value] = form.value.api_token;
    tokenDirty.value = false;
    // 旋鈕記憶 + 非敏感快取
    writeOverride(form.value.base_url, form.value.model, {
      thinking: form.value.thinking,
      reasoning_effort: form.value.reasoning_effort,
      temperature: useTemp.value ? form.value.temperature : null,
    });
    writeSettingsCache({
      model: form.value.model,
      base_url: form.value.base_url,
      thinking: form.value.thinking,
      reasoning_effort: form.value.reasoning_effort,
      temperature: useTemp.value ? form.value.temperature : null,
    });
    Message.success('已儲存模型配置');
    return true;
  } catch (e: any) {
    Message.error('儲存失敗：' + (e?.message || e));
    return false;
  } finally {
    saving.value = false;
  }
};

// 即時測試：用「當前表單值」（非已儲存）實打一次最小呼叫，結果以終端風格輸出在按鈕下方
/** 後端 /settings/test-llm 回傳形狀（ping）。 */
interface PingResult {
  ok: boolean;
  model?: string;
  base_url?: string;
  sent?: string;
  reply?: string;
  latency_ms?: number;
  tokens?: number;
  error?: string;
}
const testResult = ref<PingResult | null>(null);
const termRef = ref<InstanceType<typeof Terminal>>();
const onTest = async () => {
  testing.value = true;
  testResult.value = null;
  try {
    const r = await testLlm(buildPatch());
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

// 測試結果 → 終端輸出（ANSI 上色）。await nextTick 等 v-if 掛載 + expose 就緒
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
  if (r.tokens) t.writeln(`${ANSI.dim}tokens: ${r.tokens}${ANSI.reset}`);
  if (r.error) t.writeln(`${ANSI.red}✗ ${r.error}${ANSI.reset}`);
});

// 恢復預設：把表單還原成 config/defaults.json 最底層 openai preset（不動已儲存值，需按儲存才生效）
const onRestoreDefaults = () => {
  const p = PROVIDERS.find((x) => x.id === 'openai');
  selectedProvider.value = 'openai';
  form.value.base_url = p?.base_url ?? DEFAULT_LLM_FORM.base_url;
  form.value.model = p?.defaultModel ?? DEFAULT_LLM_FORM.model;
  form.value.thinking = p?.thinking ?? DEFAULT_LLM_FORM.thinking;
  form.value.reasoning_effort = p?.reasoning_effort ?? DEFAULT_LLM_FORM.reasoning_effort;
  useTemp.value = false;
  form.value.temperature = 0;
  // 還原 openai 時帶回 openai 已存的 token（與切換 provider 一致）
  form.value.api_token = providerTokens.value['openai'] ?? '';
  hasToken.value = !!form.value.api_token;
  tokenDirty.value = false;
  testResult.value = null;
  Message.info('已還原為專案預設配置（需按「儲存配置」才寫入後端）');
};
</script>

<template>
  <StateGuard :loading="loading">
    <div>
      <a-space class="mb-3" wrap>
        <a-tag v-if="hasToken" color="green">token ✓</a-tag>
        <a-tag v-else color="red">未設定 token</a-tag>
        <a-tag color="gray" class="font-mono">{{ form.model || 'no model' }}</a-tag>
        <a-tag :color="stubMode ? 'orange' : 'arcoblue'">{{
          stubMode ? 'stub 啟發式' : '真 LLM'
        }}</a-tag>
      </a-space>

      <a-form :model="form" layout="vertical">
        <a-form-item label="供應商">
          <a-select
            :model-value="selectedProvider"
            placeholder="選擇供應商，自動帶入 token / base_url 與 model 清單"
            @change="selectProvider"
          >
            <a-option v-for="p in PROVIDERS" :key="p.id" :value="p.id">{{ p.label }}</a-option>
          </a-select>
        </a-form-item>

        <a-form-item label="API Token">
          <a-input-password
            v-model="form.api_token"
            :placeholder="hasToken ? '已設定（留空不變更）' : '請輸入 token（sk-... / Gemini key）'"
            allow-clear
            @input="tokenDirty = true"
          />
          <template #extra>
            <span class="text-xs text-[#86909c]">
              Token 只存後端（user_settings，DB），不入 git，前端僅遮罩顯示。
            </span>
          </template>
        </a-form-item>

        <a-row :gutter="12">
          <a-col :span="12">
            <a-form-item label="Model">
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
          </a-col>
          <a-col :span="12">
            <a-form-item label="Base URL">
              <a-input v-model="form.base_url" placeholder="空＝OpenAI 預設端點" allow-clear />
            </a-form-item>
          </a-col>
        </a-row>

        <a-form-item label="Temperature">
          <a-space>
            <a-switch v-model="useTemp" />
            <span class="text-xs text-[#86909c]">{{
              useTemp ? '自訂' : 'API 預設（gpt-5 系列鎖定）'
            }}</span>
            <a-slider
              v-if="useTemp"
              v-model="form.temperature"
              :min="0"
              :max="2"
              :step="0.1"
              class="w-[220px]"
            />
            <span v-if="useTemp">{{ form.temperature ?? 0 }}</span>
          </a-space>
        </a-form-item>

        <a-form-item label="思考模式 Thinking">
          <a-radio-group v-model="form.thinking" type="button" size="small">
            <a-radio value="off">關閉</a-radio>
            <a-radio value="on">開啟</a-radio>
          </a-radio-group>
        </a-form-item>

        <a-form-item label="Reasoning effort">
          <a-radio-group v-model="form.reasoning_effort" type="button" size="small">
            <a-radio v-for="r in REASONING" :key="r" :value="r">{{ r }}</a-radio>
          </a-radio-group>
        </a-form-item>

        <a-space>
          <a-button type="primary" :loading="saving" @click="onSave">儲存配置</a-button>
          <a-button :loading="testing" @click="onTest">測試連線</a-button>
          <a-button @click="onRestoreDefaults">恢復預設</a-button>
          <span class="text-xs text-[#86909c]"
            >儲存＝寫入 user_settings；測試＝即時測當前配置（不寫入）</span
          >
        </a-space>

        <!-- 測試結果：終端風格 I/O log（xterm.js；即時測當前配置，非已儲存）-->
        <Terminal v-if="testResult" ref="termRef" class="mt-3" />
      </a-form>

      <ul class="mb-0 mt-4 pl-[18px] text-[13px] leading-[1.7] text-[#4e5969]">
        <li>
          選供應商一鍵帶入 token / base_url；Model 下拉只列 config 預設清單（不混入
          whisper/embedding 等）。
        </li>
        <li>
          「測試連線」即時測「當前螢幕上的配置」（非已儲存），完整 log 顯示在按鈕下方，耗極少
          token。
        </li>
        <li>「恢復預設」把表單還原成專案最底層預設（需再按「儲存配置」才寫入後端）。</li>
        <li>
          Reasoning effort 對齊官方支援值（none / low / medium / high /
          xhigh）；切換模型後旋鈕會記憶。
        </li>
        <li>gpt-5 系列 temperature 鎖定，建議維持「API 預設」。</li>
      </ul>
    </div>
  </StateGuard>
</template>

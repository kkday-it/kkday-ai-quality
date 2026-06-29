<script setup lang="ts">
import { ref, computed, onMounted } from 'vue';
import { isNil, uniq } from 'lodash-es';
import { Message } from '@arco-design/web-vue';
import { getSettingsRaw, saveSettings, testLlm } from '@/api';
import { StateGuard } from '@/components';
import {
  PROVIDERS,
  REASONING,
  DEFAULT_LLM_FORM,
  MODEL_META,
  MODEL_MIN_VERSION,
} from '../constants';
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
const stubMode = ref(true);
const tokenDirty = ref(false);
const useTemp = ref(false);
const loading = ref(true);
const saving = ref(false);
const testing = ref(false);

// 當前供應商的 model 下拉選項：直接讀 config/defaults.json 的 curated defaultModels。
// union 當前已選/已存 model（即使不在 curated 也顯示，如歷史保存值），再過濾版本門檻。
const modelOptions = computed(() => {
  const p = PROVIDERS.find((x) => x.id === selectedProvider.value);
  const curated = p?.defaultModels ?? [];
  const all = form.value.model ? uniq([...curated, form.value.model]) : curated;
  return all.filter((m) => modelMeetsMin(m, MODEL_MIN_VERSION));
});

// model id → 質性評價（成本/用途 hint），來自 config/defaults.json modelMeta。
const modelMeta = (id: string): string => MODEL_META[id] ?? '';

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
    hasToken.value = !!s.has_token;
    stubMode.value = !!s.stub_mode;
    // 已設定 → 欄位帶入完整明文 token（password 預設遮罩，眼睛切換顯示全文）；未動則不重送
    if (hasToken.value && s.api_token) {
      form.value.api_token = s.api_token;
      tokenDirty.value = false;
    }
    // 供應商由 base_url 反推（當前 model 已由 modelOptions union 進下拉，無需另補）
    selectedProvider.value = deriveProviderId(form.value.base_url);
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
  if (p.api_token) {
    form.value.api_token = p.api_token;
    tokenDirty.value = true;
  } else {
    form.value.api_token = ''; // 新供應商需自填 token
    tokenDirty.value = false;
  }
  form.value.model = p.defaultModel ?? p.defaultModels[0] ?? '';
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
  if (tokenDirty.value && form.value.api_token) patch.api_token = form.value.api_token;
  return patch;
}

const onSave = async () => {
  saving.value = true;
  try {
    const s = await saveSettings(buildPatch());
    hasToken.value = !!s.has_token;
    stubMode.value = !!s.stub_mode;
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

// 即時測試：用「當前表單值」（非已儲存）實打一次最小呼叫，完整結果輸出在按鈕下方 log
const testResult = ref<Record<string, unknown> | null>(null);
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
                <a-option v-for="m in modelOptions" :key="m" :value="m" :label="m">
                  <span>{{ m }}</span>
                  <span v-if="modelMeta(m)" class="ml-2 text-xs text-[#86909c]">{{
                    modelMeta(m)
                  }}</span>
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

        <!-- 測試結果完整 log（即時測當前配置；非已儲存）-->
        <div
          v-if="testResult"
          class="mt-3 rounded-lg border p-2.5 font-mono text-[12px] leading-[1.6]"
          :class="
            testResult.ok
              ? 'border-[#a3e8dd] bg-[#e8fffb] text-[#0f9b8e]'
              : 'border-[#ffccc7] bg-[#fff1f0] text-[#cf1322]'
          "
        >
          <div class="mb-1 font-bold">
            {{ testResult.ok ? '✅ 連線成功' : '❌ 連線失敗' }}
            <span v-if="testResult.latency_ms" class="font-normal">
              · {{ testResult.latency_ms }}ms</span
            >
          </div>
          <div>model：{{ testResult.model }}</div>
          <div>base_url：{{ testResult.base_url }}</div>
          <div v-if="testResult.sent">送出：{{ testResult.sent }}</div>
          <div v-if="testResult.reply">回覆：{{ testResult.reply }}</div>
          <div v-if="testResult.tokens">tokens：{{ testResult.tokens }}</div>
          <div v-if="testResult.error" class="whitespace-pre-wrap">
            錯誤：{{ testResult.error }}
          </div>
        </div>
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

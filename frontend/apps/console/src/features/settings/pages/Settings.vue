<script setup lang="ts">
import { ref, computed, onMounted } from 'vue';
import { isNil } from 'lodash-es';
import { Message } from '@arco-design/web-vue';
import { getSettingsRaw, saveSettings } from '@/api';
import { StateGuard } from '@/components';
import { PROVIDERS, REASONING } from '../constants';
import {
  modelKey,
  readOverrides,
  writeOverride,
  mergeProviderModels,
  persistProviderModels,
  readSettingsCache,
  writeSettingsCache,
  deriveProviderId,
  deriveBackendProvider,
} from '../utils';

// 供應商 → model 列表（preset merge localStorage 自訂值；持久化邏輯見 utils/storage.util）
const providerModels = ref<Record<string, string[]>>({});

const form = ref({
  model: 'gpt-5.5',
  base_url: '',
  api_token: '',
  temperature: 0 as number,
  thinking: 'off',
  reasoning_effort: 'medium',
});
const selectedProvider = ref('openai');
const hasToken = ref(false);
const stubMode = ref(true);
const tokenDirty = ref(false);
const useTemp = ref(false);
const loading = ref(true);
const saving = ref(false);

// 當前供應商的 model 下拉選項
const modelOptions = computed(() => providerModels.value[selectedProvider.value] ?? []);

onMounted(async () => {
  providerModels.value = mergeProviderModels(PROVIDERS);
  // 1) localStorage 快取（非敏感欄位）
  const cached = readSettingsCache<typeof form.value>();
  if (cached) {
    Object.assign(form.value, cached);
    useTemp.value = !isNil(cached.temperature);
  }
  // 2) 後端真值蓋上（raw 端點回明文 token，眼睛切換即可看全文）；舊值歸一到新選項集
  try {
    const s = await getSettingsRaw();
    form.value.model = s.model ?? 'gpt-5.5';
    form.value.base_url = s.base_url ?? '';
    form.value.thinking = s.thinking === 'on' || s.thinking === 'off' ? s.thinking : 'off';
    form.value.reasoning_effort = REASONING.includes(s.reasoning_effort) ? s.reasoning_effort : 'medium';
    form.value.temperature = s.temperature ?? 0;
    useTemp.value = !isNil(s.temperature);
    hasToken.value = !!s.has_token;
    stubMode.value = !!s.stub_mode;
    // 已設定 → 欄位帶入完整明文 token（password 預設遮罩，眼睛切換顯示全文）；未動則不重送
    if (hasToken.value && s.api_token) {
      form.value.api_token = s.api_token;
      tokenDirty.value = false;
    }
    // 供應商由 base_url 反推；若當前 model 不在該供應商列表，補進去（讓下拉顯示）
    selectedProvider.value = deriveProviderId(form.value.base_url);
    ensureModelListed();
  } catch {
    // 後端 raw 端點未就緒（如後端未重啟）時靜默降級：沿用 localStorage 快取 / default，不阻斷面板
  } finally {
    loading.value = false;
  }
});

// 把當前 model 補進當前供應商列表（去重）
function ensureModelListed(): void {
  const id = selectedProvider.value;
  const list = providerModels.value[id] ?? [];
  if (form.value.model && !list.includes(form.value.model)) {
    providerModels.value[id] = [...list, form.value.model];
  }
}


// 切換供應商：帶入 base_url / token；model 預設選該供應商第一個；旋鈕沿 preset + override
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
  form.value.model = (providerModels.value[p.id] ?? [])[0] ?? '';
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

const onSave = async () => {
  saving.value = true;
  try {
    const patch: Record<string, unknown> = {
      provider: deriveBackendProvider(form.value.base_url),
      model: form.value.model,
      base_url: form.value.base_url,
      thinking: form.value.thinking,
      reasoning_effort: form.value.reasoning_effort,
      temperature: useTemp.value ? form.value.temperature : null,
    };
    // 僅當使用者輸入新 token（dirty、非空）才送；form 內已是明文，不會誤送遮罩值
    if (tokenDirty.value && form.value.api_token) {
      patch.api_token = form.value.api_token;
    }
    const s = await saveSettings(patch);
    hasToken.value = !!s.has_token;
    stubMode.value = !!s.stub_mode;
    tokenDirty.value = false;
    // 手動輸入的新 model 持久化進該供應商列表
    ensureModelListed();
    persistProviderModels(providerModels.value);
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
  } catch (e: any) {
    Message.error('儲存失敗：' + (e?.message || e));
  } finally {
    saving.value = false;
  }
};
</script>

<template>
  <StateGuard :loading="loading">
    <div class="max-w-[760px]">
    <a-card>
      <template #title>
        <span>⚙️ LLM 模型配置（設定 Settings）</span>
      </template>
      <template #extra>
        <a-tag v-if="hasToken" color="green">token ✓</a-tag>
        <a-tag v-else color="red">未設定 token</a-tag>
        <a-tag color="gray" class="ml-1.5 font-mono">{{ form.model || 'no model' }}</a-tag>
        <a-tag :color="stubMode ? 'orange' : 'arcoblue'" class="ml-1.5">
          {{ stubMode ? 'stub 啟發式' : '真 LLM' }}
        </a-tag>
      </template>

      <a-form :model="form" layout="vertical">
        <a-form-item label="供應商">
          <a-select :model-value="selectedProvider" placeholder="選擇供應商，自動帶入 token / base_url 與 model 清單" @change="selectProvider">
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
              Token 只存後端 data/settings.json（gitignore，不入 git），前端僅遮罩顯示。
            </span>
          </template>
        </a-form-item>

        <a-row :gutter="12">
          <a-col :span="12">
            <a-form-item label="Model">
              <a-select v-model="form.model" allow-create allow-clear placeholder="選擇或手動輸入（臨時，儲存後併入清單）">
                <a-option v-for="m in modelOptions" :key="m" :value="m">{{ m }}</a-option>
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
            <span class="text-xs text-[#86909c]">{{ useTemp ? '自訂' : 'API 預設（gpt-5 系列鎖定）' }}</span>
            <a-slider v-if="useTemp" v-model="form.temperature" :min="0" :max="2" :step="0.1" class="w-[220px]" />
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
          <a-button type="primary" :loading="saving" @click="onSave">儲存設定</a-button>
          <span class="text-xs text-[#86909c]">寫入後端 data/settings.json，立即生效於判決鏈路</span>
        </a-space>
      </a-form>
    </a-card>

    <a-card class="mt-4" title="說明">
      <ul class="m-0 pl-[18px] text-[13px] leading-[1.7] text-[#4e5969]">
        <li>選供應商一鍵帶入 token / base_url 與該供應商 model 清單；GPT 對齊 OpenAI 官方 gpt-5.5 / gpt-5.4 / mini / nano。</li>
        <li>Model 可從下拉選，也可手動輸入新 model；儲存後該 model 會累積進對應供應商的清單。</li>
        <li>Reasoning effort 對齊官方 GPT-5.4 支援值（none / low / medium / high / xhigh）；切換模型後旋鈕會記憶。</li>
        <li>gpt-5 系列 temperature 鎖定，建議維持「API 預設」。</li>
      </ul>
    </a-card>
    </div>
  </StateGuard>
</template>


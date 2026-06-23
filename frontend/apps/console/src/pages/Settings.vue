<script setup lang="ts">
import { ref, onMounted } from 'vue';
import { Message } from '@arco-design/web-vue';
import { getSettings, saveSettings } from '../api/client';

// 快速套用模型 preset（選後自動填 provider/model/base_url）
const PRESETS = [
  { label: 'OpenAI — gpt-5-mini（預設）', provider: 'openai', model: 'gpt-5-mini', base_url: '' },
  { label: 'OpenAI — gpt-4o-mini', provider: 'openai', model: 'gpt-4o-mini', base_url: '' },
  { label: 'OpenAI — gpt-4o', provider: 'openai', model: 'gpt-4o', base_url: '' },
  { label: 'Gemini — gemini-2.5-flash', provider: 'gemini', model: 'gemini-2.5-flash', base_url: 'https://generativelanguage.googleapis.com/v1beta/openai/' },
];
const REASONING = ['default', 'none', 'minimal', 'low', 'medium', 'high'];

const form = ref({
  provider: 'openai',
  model: 'gpt-5-mini',
  base_url: '',
  api_token: '',
  temperature: null as number | null,
  thinking: 'default',
  reasoning_effort: 'default',
});
const tokenMasked = ref('');
const hasToken = ref(false);
const stubMode = ref(true);
const tokenDirty = ref(false); // 使用者是否動過 token 欄位
const useTemp = ref(false);
const loading = ref(true);
const saving = ref(false);

onMounted(async () => {
  try {
    const s = await getSettings();
    form.value.provider = s.provider ?? 'openai';
    form.value.model = s.model ?? 'gpt-5-mini';
    form.value.base_url = s.base_url ?? '';
    form.value.thinking = s.thinking ?? 'default';
    form.value.reasoning_effort = s.reasoning_effort ?? 'default';
    form.value.temperature = s.temperature ?? null;
    useTemp.value = s.temperature !== null && s.temperature !== undefined;
    tokenMasked.value = s.api_token ?? '';
    hasToken.value = !!s.has_token;
    stubMode.value = !!s.stub_mode;
  } finally {
    loading.value = false;
  }
});

const applyPreset = (label: string) => {
  const p = PRESETS.find((x) => x.label === label);
  if (!p) return;
  form.value.provider = p.provider;
  form.value.model = p.model;
  form.value.base_url = p.base_url;
};

const onSave = async () => {
  saving.value = true;
  try {
    const patch: Record<string, unknown> = {
      provider: form.value.provider,
      model: form.value.model,
      base_url: form.value.base_url,
      thinking: form.value.thinking,
      reasoning_effort: form.value.reasoning_effort,
      temperature: useTemp.value ? form.value.temperature : null,
    };
    // 僅當使用者輸入新 token（非空、非遮罩）才送，避免誤清既有 key
    if (tokenDirty.value && form.value.api_token && !form.value.api_token.includes('…')) {
      patch.api_token = form.value.api_token;
    }
    const s = await saveSettings(patch);
    tokenMasked.value = s.api_token ?? '';
    hasToken.value = !!s.has_token;
    stubMode.value = !!s.stub_mode;
    form.value.api_token = '';
    tokenDirty.value = false;
    Message.success('已儲存模型配置');
  } catch (e: any) {
    Message.error('儲存失敗：' + (e?.message || e));
  } finally {
    saving.value = false;
  }
};
</script>

<template>
  <a-spin v-if="loading" style="display: block; text-align: center; padding: 60px" />
  <div v-else style="max-width: 760px">
    <a-card>
      <template #title>
        <span>⚙️ LLM 模型配置（設定 Settings）</span>
      </template>
      <template #extra>
        <a-tag v-if="hasToken" color="green">token ✓</a-tag>
        <a-tag v-else color="red">未設定 token</a-tag>
        <a-tag :color="stubMode ? 'orange' : 'arcoblue'" style="margin-left: 6px">
          {{ stubMode ? 'stub 啟發式' : '真 LLM' }}
        </a-tag>
      </template>

      <a-form :model="form" layout="vertical">
        <a-form-item label="快速套用模型">
          <a-select placeholder="選擇 preset 自動填入 provider / model / base_url" allow-clear @change="applyPreset">
            <a-option v-for="p in PRESETS" :key="p.label" :value="p.label">{{ p.label }}</a-option>
          </a-select>
        </a-form-item>

        <a-row :gutter="12">
          <a-col :span="8">
            <a-form-item label="Provider">
              <a-select v-model="form.provider">
                <a-option value="openai">openai</a-option>
                <a-option value="gemini">gemini</a-option>
                <a-option value="azure">azure</a-option>
                <a-option value="custom">custom</a-option>
              </a-select>
            </a-form-item>
          </a-col>
          <a-col :span="16">
            <a-form-item label="Model">
              <a-input v-model="form.model" placeholder="gpt-5-mini" />
            </a-form-item>
          </a-col>
        </a-row>

        <a-form-item label="API Token">
          <a-input-password
            v-model="form.api_token"
            :placeholder="hasToken ? '已設定 token（留空不變更）' : '貼上 API Key（sk-... / Gemini key）'"
            allow-clear
            @input="tokenDirty = true"
          />
          <template #extra>
            <span style="color: #86909c; font-size: 12px">
              Token 只存後端 data/settings.json（gitignore，不入 git），前端僅遮罩顯示。
            </span>
          </template>
        </a-form-item>

        <a-form-item label="Base URL（空＝OpenAI 預設端點）">
          <a-input v-model="form.base_url" placeholder="https://... （Gemini/自架端點才填）" allow-clear />
        </a-form-item>

        <a-form-item label="Temperature">
          <a-space>
            <a-switch v-model="useTemp" />
            <span style="color: #86909c; font-size: 12px">{{ useTemp ? '自訂' : 'API 預設（gpt-5 系列鎖定）' }}</span>
            <a-slider v-if="useTemp" v-model="form.temperature" :min="0" :max="2" :step="0.1" style="width: 220px" />
            <span v-if="useTemp">{{ form.temperature ?? 0 }}</span>
          </a-space>
        </a-form-item>

        <a-row :gutter="12">
          <a-col :span="12">
            <a-form-item label="思考模式 Thinking">
              <a-radio-group v-model="form.thinking" type="button" size="small">
                <a-radio value="default">預設</a-radio>
                <a-radio value="on">開啟</a-radio>
                <a-radio value="off">關閉</a-radio>
              </a-radio-group>
            </a-form-item>
          </a-col>
          <a-col :span="12">
            <a-form-item label="Reasoning effort">
              <a-select v-model="form.reasoning_effort">
                <a-option v-for="r in REASONING" :key="r" :value="r">{{ r }}</a-option>
              </a-select>
            </a-form-item>
          </a-col>
        </a-row>

        <a-space>
          <a-button type="primary" :loading="saving" @click="onSave">儲存設定</a-button>
          <span style="color: #86909c; font-size: 12px">寫入後端 data/settings.json，立即生效於判決鏈路</span>
        </a-space>
      </a-form>
    </a-card>

    <a-card style="margin-top: 16px" title="說明">
      <ul style="color: #4e5969; font-size: 13px; line-height: 1.7; margin: 0; padding-left: 18px">
        <li>判決鏈路（classify / adequacy）依此配置呼叫 LLM；無 token → stub 啟發式（零成本走通流程）。</li>
        <li>OpenAI key 已配置（gpt-5-mini），售前售後進線判定可用真 LLM。</li>
        <li>切換 Gemini／自架模型：選 preset 或手填 provider + model + base_url + token。</li>
        <li>gpt-5 系列 temperature 鎖定，建議維持「API 預設」。</li>
      </ul>
    </a-card>
  </div>
</template>

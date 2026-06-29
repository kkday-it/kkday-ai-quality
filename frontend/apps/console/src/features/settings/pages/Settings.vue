<script setup lang="ts">
import { ref, computed, onMounted } from 'vue';
import { isNil, uniq } from 'lodash-es';
import { Message } from '@arco-design/web-vue';
import { getSettingsRaw, saveSettings, testLlm, listModels } from '@/api';
import { StateGuard } from '@/components';
import {
  PROVIDERS,
  REASONING,
  DEFAULT_LLM_FORM,
  MODEL_META,
  MODEL_MIN_VERSION,
  STAGES,
} from '../constants';
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
  modelMeetsMin,
} from '../utils';

// 供應商 → model 列表（preset merge localStorage 自訂值；持久化邏輯見 utils/storage.util）
const providerModels = ref<Record<string, string[]>>({});

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

// 當前供應商的 model 下拉選項（過濾 ≥ 門檻版本；gpt-* 才受限，非 gpt 一律放行）
const modelOptions = computed(() =>
  (providerModels.value[selectedProvider.value] ?? []).filter((m) =>
    modelMeetsMin(m, MODEL_MIN_VERSION),
  ),
);

// Model 下拉動態載入：開下拉時打 /api/settings/models 撈即時清單，過濾後合併進 curated。
// 成本/評價（消耗）由 modelMeta 提供——API 無此資訊，故 hybrid：動態 id + 靜態評價。
const modelsLoading = ref(false);
let modelsFetched = false;
const modelMeta = (id: string): string => MODEL_META[id] ?? '';

async function onModelDropdown(visible: boolean): Promise<void> {
  if (!visible || modelsFetched || !hasToken.value) return; // 未開 / 已抓過 / 無 token → 略過
  modelsLoading.value = true;
  try {
    const { models } = await listModels();
    const live = (models ?? []).filter((m) => modelMeetsMin(m, MODEL_MIN_VERSION));
    if (live.length) {
      const id = selectedProvider.value;
      // curated 在前、API 新增者接後（uniq 保留首次出現順序，維持弱→強排序）
      providerModels.value[id] = uniq([...(providerModels.value[id] ?? []), ...live]);
      persistProviderModels(providerModels.value);
    }
    modelsFetched = true;
  } catch {
    // 失敗靜默：保留 curated 清單，不阻斷選擇
  } finally {
    modelsLoading.value = false;
  }
}

// 各階段覆寫（稀疏）：{ classify: { model, reasoning_effort }, ... }；空＝繼承全域，不寫入。
const stageOverrides = ref<Record<string, Record<string, string>>>({});
const stageVal = (stage: string, key: string): string => stageOverrides.value[stage]?.[key] ?? '';
function setStageVal(stage: string, key: string, v: string): void {
  const next = { ...stageOverrides.value };
  const cur = { ...(next[stage] ?? {}) };
  if (v) cur[key] = v;
  else delete cur[key]; // 清空＝回到繼承全域
  if (Object.keys(cur).length) next[stage] = cur;
  else delete next[stage];
  stageOverrides.value = next;
}

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
    stageOverrides.value = (s.stage_overrides as Record<string, Record<string, string>>) ?? {};
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

// 切換供應商：帶入 base_url / token；model 取顯式 defaultModel（缺省回退清單首項）；旋鈕沿 preset + override
const selectProvider = (id: unknown) => {
  const p = PROVIDERS.find((x) => x.id === String(id));
  if (!p) return;
  selectedProvider.value = p.id;
  modelsFetched = false; // 換供應商 → 下次開下拉重新抓該供應商的即時清單
  form.value.base_url = p.base_url;
  if (p.api_token) {
    form.value.api_token = p.api_token;
    tokenDirty.value = true;
  } else {
    form.value.api_token = ''; // 新供應商需自填 token
    tokenDirty.value = false;
  }
  form.value.model = p.defaultModel ?? (providerModels.value[p.id] ?? [])[0] ?? '';
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
      stage_overrides: stageOverrides.value, // 稀疏覆寫（後端 sanitize）
    };
    // 僅當使用者輸入新 token（dirty、非空）才送；form 內已是明文，不會誤送遮罩值
    if (tokenDirty.value && form.value.api_token) {
      patch.api_token = form.value.api_token;
    }
    const s = await saveSettings(patch);
    hasToken.value = !!s.has_token;
    stubMode.value = !!s.stub_mode;
    tokenDirty.value = false;
    modelsFetched = false; // 新 token 可能有不同可用 model → 下次開下拉重新抓
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
    return true;
  } catch (e: any) {
    Message.error('儲存失敗：' + (e?.message || e));
    return false;
  } finally {
    saving.value = false;
  }
};

// 測試連線：先存當前選擇（test_llm 讀「已儲存」設定）→ 再實打一次最小 LLM 呼叫，回報成功/失敗
const onTest = async () => {
  testing.value = true;
  try {
    // 先儲存，確保測的是螢幕上選的 model/設定，而非上次儲存的舊值
    if (!(await onSave())) return; // 儲存失敗則不測（避免測到舊設定誤導）
    const r = await testLlm();
    if (r.ok)
      Message.success(`連線成功（${r.model}${r.latency_ms ? ' · ' + r.latency_ms + 'ms' : ''}）`);
    else Message.error('連線失敗：' + (r.error || '未知錯誤'));
  } catch (e: any) {
    Message.error('測試失敗：' + (e?.message || e));
  } finally {
    testing.value = false;
  }
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
                :loading="modelsLoading"
                placeholder="選擇或手動輸入（臨時，儲存後併入清單）"
                @popup-visible-change="onModelDropdown"
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

        <a-form-item label="各階段覆寫（選填，留空＝繼承全域）">
          <div class="w-full space-y-2">
            <div
              v-for="st in STAGES"
              :key="st.id"
              class="rounded-md border border-[var(--color-neutral-3)] p-3"
            >
              <div class="mb-2 text-sm font-medium">
                {{ st.label }}
                <span class="ml-2 text-xs font-normal text-[#86909c]">{{ st.desc }}</span>
              </div>
              <a-row :gutter="12">
                <a-col :span="14">
                  <a-select
                    :model-value="stageVal(st.id, 'model')"
                    allow-clear
                    placeholder="繼承全域 model"
                    @change="(v) => setStageVal(st.id, 'model', String(v ?? ''))"
                  >
                    <a-option v-for="m in modelOptions" :key="m" :value="m" :label="m">
                      <span>{{ m }}</span>
                      <span v-if="modelMeta(m)" class="ml-2 text-xs text-[#86909c]">{{
                        modelMeta(m)
                      }}</span>
                    </a-option>
                  </a-select>
                </a-col>
                <a-col :span="10">
                  <a-select
                    :model-value="stageVal(st.id, 'reasoning_effort')"
                    allow-clear
                    placeholder="繼承全域 effort"
                    @change="(v) => setStageVal(st.id, 'reasoning_effort', String(v ?? ''))"
                  >
                    <a-option v-for="r in REASONING" :key="r" :value="r">{{ r }}</a-option>
                  </a-select>
                </a-col>
              </a-row>
            </div>
          </div>
        </a-form-item>

        <a-space>
          <a-button type="primary" :loading="saving" @click="onSave">儲存配置</a-button>
          <a-button :loading="testing" @click="onTest">測試連線</a-button>
          <span class="text-xs text-[#86909c]">寫入後端 user_settings，立即生效於判決鏈路</span>
        </a-space>
      </a-form>

      <ul class="mb-0 mt-4 pl-[18px] text-[13px] leading-[1.7] text-[#4e5969]">
        <li>
          選供應商一鍵帶入 token / base_url 與該供應商 model 清單；GPT 對齊 OpenAI 官方 gpt-5.5 /
          gpt-5.4 / mini / nano。
        </li>
        <li>Model 可從下拉選，也可手動輸入新 model；儲存後該 model 會累積進對應供應商的清單。</li>
        <li>
          Reasoning effort 對齊官方 GPT-5.4 支援值（none / low / medium / high /
          xhigh）；切換模型後旋鈕會記憶。
        </li>
        <li>gpt-5 系列 temperature 鎖定，建議維持「API 預設」。</li>
        <li>「測試連線」會先儲存當前選擇，再實打一次極短呼叫，回報連線成功/失敗（耗極少 token）。</li>
      </ul>
    </div>
  </StateGuard>
</template>

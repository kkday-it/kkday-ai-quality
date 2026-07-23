<script setup lang="ts">
import { computed, watch } from 'vue';
import { isNil } from 'lodash-es';
import {
  ALL_THINKING_MODES,
  capabilitiesFor,
  MODEL_MIN_VERSION,
  PROVIDERS,
  REASONING,
} from '@/features/settings/constants';
import { modelMeetsMin } from '@/features/settings/utils';
import type { LlmAreaDefault, LlmReasoningEffort, LlmThinking } from '@/features/settings/types';

/** LLM 旋鈕組（model / thinking / reasoning_effort / temperature）：canonical 共用元件，
 * 供設定面板「功能區默認」與各功能區（prejudge/prompt_debug/sandbox）本次執行覆寫共用同一組控件與
 * 正規化邏輯，不得各自重做一套（見 .claude/rules/frontend-vue.md「同語義控件跨頁一致」）。
 * 2026-07-23 依三供應商官方文件全面重寫：OpenAI/Gemini 沒有獨立 thinking 開關（reasoning_effort
 * 本身即完整控制面，`capabilities.thinkingControl==='effortOnly'`），ByteDance/Ark 才有真實原生三態
 * thinking 開關（`thinkingControl==='nativeSwitch'`，見 capabilities.thinkingModes）——兩種供應商的
 * 控件形狀因此不同，由 `capabilities.thinkingControl` 分流渲染，取代舊版全供應商共用同一套假想
 * 「Thinking on/off + Reasoning effort」二段式控件。各家官方依據見 capabilities.docs。 */
type Knobs = Pick<LlmAreaDefault, 'model' | 'thinking' | 'reasoning_effort' | 'temperature'>;

const props = defineProps<{
  modelValue: Knobs;
  /** 決定 model 下拉清單與可配參數能力來源；由 LlmConfigPicker 或呼叫端固定帶入。 */
  provider: string;
}>();
const emit = defineEmits<{
  (e: 'update:modelValue', value: Knobs): void;
}>();

/** 當前供應商的 model 下拉（{id,desc}）；已選/歷史 model 不在 curated 時補一筆，再過濾版本門檻。 */
const modelOptions = computed(() => {
  const p = PROVIDERS.find((x) => x.id === props.provider);
  const curated = p?.defaultModels ?? [];
  const has = curated.some((m) => m.id === props.modelValue.model);
  const all =
    props.modelValue.model && !has ? [...curated, { id: props.modelValue.model }] : curated;
  return all.filter((m) => modelMeetsMin(m.id, MODEL_MIN_VERSION));
});

/** 目前選中 model 的質性描述（成本/用途），供 Model 控件下方常駐 hint 用——選單關閉後看不到
 * a-option 內的 desc，故另外拉出來常駐顯示，而非僅下拉展開時可見。 */
const selectedModelDesc = computed(
  () => modelOptions.value.find((m) => m.id === props.modelValue.model)?.desc ?? '',
);

const capabilities = computed(() => capabilitiesFor(props.modelValue.model, props.provider));

/** 按鈕清單恆帶 'default' 排最前面，讓「沒有客製化、用 API 預設」有明確可視選中的按鈕，
 * 不再出現「整組沒有任何按鈕高亮」這種曖昧狀態。'default' 是 UI 層的顯式選擇、非真實 API 值
 * （client.py 會在組參數時濾掉，等同不送該欄位），故不受 capabilities 值域限制、恆可選。 */
const THINKING_CHOICES = ['default', ...ALL_THINKING_MODES];

/** reasoning_effort 按鈕清單：只顯示「這個供應商底下至少一個 model 用得到」的值（provider 級預設、
 * 未套用個別 model 覆寫）——跨供應商本來就沒有的值（如 ByteDance 沒有 xhigh）直接不顯示，不是灰掉；
 * 同供應商內個別 model 進一步限縮的值（如 gpt-5-mini 不吃 none/xhigh）才用 disabled 灰掉，兩種情境視覺
 * 上分開處理，避免「永遠灰掉的按鈕」造成困惑。 */
const providerReasoningOptions = computed(
  () => PROVIDERS.find((p) => p.id === props.provider)?.reasoningEffortOptions ?? REASONING,
);
const REASONING_CHOICES = computed(() => ['default', ...providerReasoningOptions.value]);

/** 是否「正在推理」：effortOnly 供應商（OpenAI/Gemini）沒有獨立開關，看 reasoning_effort 是否為
 * 非 none 的實際值；nativeSwitch 供應商（ByteDance）看 thinking 是否為 enabled/auto（disabled 明確
 * 不推理）。取代舊版寫死的 `thinking === 'on'` 判斷（該值域已不適用於 effortOnly 供應商）。 */
const isReasoningActive = computed(() => {
  if (capabilities.value.thinkingControl === 'nativeSwitch') {
    return props.modelValue.thinking === 'enabled' || props.modelValue.thinking === 'auto';
  }
  const eff = props.modelValue.reasoning_effort;
  return Boolean(eff) && eff !== 'none' && eff !== 'default';
});

/** Reasoning effort 控件是否應 disable：僅 nativeSwitch 供應商在 thinking='disabled' 時成立
 * （官方確認該狀態不可併送 reasoning_effort）；effortOnly 供應商沒有這個概念，恆可選。 */
const reasoningEffortDisabled = computed(
  () => capabilities.value.thinkingControl === 'nativeSwitch' && props.modelValue.thinking === 'disabled',
);

/** temperature 鎖定：該 model 不論 thinking 狀態一律鎖定（temperatureAlwaysLocked，如 ByteDance
 * seed-2-0-lite 系列伺服器端靜默忽略自訂值，2026-07-23 實測驗證），或正在推理時鎖定
 * （temperatureLockedWhenThinking，如 OpenAI reasoning model）→ 鎖 1、不可修改。 */
const tempLocked = computed(
  () =>
    capabilities.value.temperatureAlwaysLocked ||
    (isReasoningActive.value && capabilities.value.temperatureLockedWhenThinking),
);
const useTemp = computed({
  get: () => !isNil(props.modelValue.temperature),
  set: (v: boolean) => patch({ temperature: v ? (props.modelValue.temperature ?? 0) : null }),
});

function patch(partial: Partial<Knobs>): void {
  emit('update:modelValue', { ...props.modelValue, ...partial });
}

// 鎖定成立即強制 temperature=鎖定值並視為自訂（送出該值）；immediate 讓載入既有默認/切換 provider 時也一併校正。
watch(
  tempLocked,
  (locked) => {
    if (locked && props.modelValue.temperature !== capabilities.value.lockedTemperatureValue) {
      patch({ temperature: capabilities.value.lockedTemperatureValue });
    }
  },
  { immediate: true },
);

// 切換供應商可能連帶降低 maxTemperature（如 OpenAI/Gemini 的 2 → ByteDance 的 1）；
// 既有自訂值超出新上限時夾回上限，避免送出該供應商 API 會拒絕的值。
watch(
  () => capabilities.value.maxTemperature,
  (max) => {
    if (!tempLocked.value && useTemp.value && (props.modelValue.temperature ?? 0) > max) {
      patch({ temperature: max });
    }
  },
);

// 切換 model/provider 可能改變 reasoningEffortOptions（如 ByteDance 官方值域無 xhigh，OpenAI 卻有）；
// 既有存值不在新選項清單時（殘留舊資料）重置為 medium——三家 reasoningEffortOptions 皆含此檔，
// 比退回「不送此參數」更明確可預期；immediate 讓載入既有默認/切換 provider 時也一併校正。
watch(
  () => capabilities.value.reasoningEffortOptions,
  (options) => {
    const cur = props.modelValue.reasoning_effort;
    if (cur && cur !== 'default' && !options.includes(cur)) {
      patch({
        reasoning_effort: (options.includes('medium') ? 'medium' : options[0]) as LlmReasoningEffort,
      });
    }
  },
  { immediate: true },
);
</script>

<template>
  <div class="flex flex-col gap-1">
    <a-form-item label="Model" content-flex label-col-flex="108px" :label-col-style="{ whiteSpace: 'nowrap' }">
      <div class="flex flex-col gap-1">
        <a-select
          :model-value="modelValue.model"
          allow-create
          allow-clear
          placeholder="從預設清單選（也可手動輸入臨時 model）"
          :trigger-props="{ autoFitPopupWidth: false, autoFitPopupMinWidth: true }"
          @update:model-value="(v) => patch({ model: String(v) })"
        >
          <a-option v-for="m in modelOptions" :key="m.id" :value="m.id" :label="m.id">
            <span>{{ m.id }}</span>
            <span v-if="m.desc" class="ml-2 whitespace-nowrap text-xs text-[#86909c]">{{ m.desc }}</span>
          </a-option>
        </a-select>
        <span class="text-xs text-[#86909c]">{{ selectedModelDesc || '清單外手動輸入的臨時 model，成本/用途未知' }}</span>
      </div>
    </a-form-item>

    <a-form-item
      v-if="capabilities.thinkingControl === 'nativeSwitch'"
      label="Thinking"
      content-flex
      label-col-flex="108px"
      :label-col-style="{ whiteSpace: 'nowrap' }"
    >
      <div class="flex flex-col gap-1">
        <a-radio-group
          :model-value="modelValue.thinking || 'default'"
          type="button"
          size="small"
          @update:model-value="(v) => patch({ thinking: v as LlmThinking })"
        >
          <a-radio
            v-for="m in THINKING_CHOICES"
            :key="m"
            :value="m"
            :disabled="m !== 'default' && !capabilities.thinkingModes.includes(m)"
          >{{ m }}</a-radio>
        </a-radio-group>
        <span class="text-xs text-[#86909c]">{{
          modelValue.thinking === 'disabled'
            ? '關閉：原生開關，不送推理參數'
            : modelValue.thinking === 'auto'
              ? '自動：模型自行判斷是否需要思考'
              : modelValue.thinking === 'enabled'
                ? '開啟：可搭配下方 Reasoning effort'
                : 'Default：不送開關，交給 API 自行決定（2026-07-23 實測 seed-2-0-lite 此狀態下 API 預設為開啟思考，其他 model 未逐一驗證）'
        }}</span>
      </div>
    </a-form-item>

    <a-form-item label="Temperature" content-flex label-col-flex="108px" :label-col-style="{ whiteSpace: 'nowrap' }">
      <div class="flex flex-col gap-1">
        <a-space :wrap="false" class="w-full">
          <a-switch :model-value="useTemp" :disabled="tempLocked" @update:model-value="(v) => (useTemp = Boolean(v))" />
          <a-slider
            v-if="useTemp && !tempLocked"
            :model-value="modelValue.temperature ?? 0"
            :min="0"
            :max="capabilities.maxTemperature"
            :step="0.1"
            class="w-[140px]"
            @update:model-value="(v) => patch({ temperature: v as number })"
          />
          <span v-if="useTemp" class="whitespace-nowrap">{{ tempLocked ? capabilities.lockedTemperatureValue : (modelValue.temperature ?? 0) }}</span>
        </a-space>
        <span class="text-xs text-[#86909c]">{{
          tempLocked
            ? capabilities.temperatureAlwaysLocked
              ? `鎖定 ${capabilities.lockedTemperatureValue}（此 model 無法完全關閉推理，temperature 固定，自訂值會被伺服器拒絕或忽略）`
              : `鎖定 ${capabilities.lockedTemperatureValue}（推理生效中，官方 API 僅接受預設值）`
            : useTemp
              ? '自訂'
              : capabilities.temperatureLockedWhenThinking
                ? `API 預設（推理生效時會鎖定為 ${capabilities.lockedTemperatureValue}）`
                : 'API 預設'
        }}</span>
      </div>
    </a-form-item>

    <a-form-item
      v-if="capabilities.supportsThinking"
      label="Reasoning effort"
      content-flex
      label-col-flex="108px"
      :label-col-style="{ whiteSpace: 'nowrap' }"
    >
      <div class="flex flex-col gap-1">
        <a-radio-group
          :model-value="modelValue.reasoning_effort || 'default'"
          type="button"
          size="small"
          :disabled="reasoningEffortDisabled"
          @update:model-value="(v) => patch({ reasoning_effort: String(v) as LlmReasoningEffort })"
        >
          <a-radio
            v-for="r in REASONING_CHOICES"
            :key="r"
            :value="r"
            :disabled="r !== 'default' && !capabilities.reasoningEffortOptions.includes(r)"
          >{{ r }}</a-radio>
        </a-radio-group>
        <span class="text-xs text-[#86909c]">{{
          reasoningEffortDisabled
            ? capabilities.reasoningOffHint || '不可用：目前狀態不支援送出 reasoning_effort'
            : modelValue.reasoning_effort && modelValue.reasoning_effort !== 'default'
              ? `將送出 reasoning_effort="${modelValue.reasoning_effort}"${modelValue.reasoning_effort === 'none' ? '（等同不啟用推理）' : ''}`
              : 'Default：不送此參數，使用該 model 的 API 預設值'
        }}</span>
      </div>
    </a-form-item>

    <div v-if="Object.keys(capabilities.docs).length" class="flex flex-wrap gap-x-3 gap-y-1 pt-1 text-xs">
      <a
        v-for="(url, label) in capabilities.docs"
        :key="url"
        :href="url"
        target="_blank"
        rel="noopener noreferrer"
        class="text-[rgb(var(--primary-6))] hover:underline"
      >{{ label }} ↗</a>
    </div>
  </div>
</template>

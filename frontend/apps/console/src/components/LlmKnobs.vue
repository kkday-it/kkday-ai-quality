<script setup lang="ts">
import { computed, watch } from 'vue';
import { isNil } from 'lodash-es';
import {
  capabilitiesFor,
  MODEL_MIN_VERSION,
  PROVIDERS,
} from '@/features/settings/constants';
import { modelMeetsMin } from '@/features/settings/utils';
import type { LlmAreaDefault, LlmReasoningEffort } from '@/features/settings/types';

/** LLM 旋鈕組（model / thinking / reasoning_effort / temperature）：canonical 共用元件，
 * 供設定面板「功能區默認」與各功能區（prejudge/prompt_debug/sandbox）本次執行覆寫共用同一組控件與
 * 正規化邏輯，不得各自重做一套（見 .claude/rules/frontend-vue.md「同語義控件跨頁一致」）。
 * 可配參數能力（thinking 是否支援 / reasoning_effort 值域 / temperature 鎖定規則）由 `provider`
 * 決定所屬供應商，取代舊寫死的 `tempLocked = provider === 'openai'`（見 capabilitiesFor）。 */
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

const capabilities = computed(() => capabilitiesFor(props.modelValue.model, props.provider));
/** 思考模式開啟且該 model 鎖定 temperature（見 capabilitiesFor）→ 鎖 1、不可修改。 */
const tempLocked = computed(
  () => props.modelValue.thinking === 'on' && capabilities.value.temperatureLockedWhenThinking,
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
</script>

<template>
  <div>
    <a-form-item label="Model" content-flex label-col-flex="80px">
      <a-select
        :model-value="modelValue.model"
        allow-create
        allow-clear
        placeholder="從預設清單選（也可手動輸入臨時 model）"
        @update:model-value="(v) => patch({ model: String(v) })"
      >
        <a-option v-for="m in modelOptions" :key="m.id" :value="m.id" :label="m.id">
          <span>{{ m.id }}</span>
          <span v-if="m.desc" class="ml-2 text-xs text-[#86909c]">{{ m.desc }}</span>
        </a-option>
      </a-select>
    </a-form-item>

    <a-row :gutter="12">
      <a-col :flex="'auto'">
        <a-form-item label="Temperature" content-flex label-col-flex="80px">
          <a-space>
            <a-switch :model-value="useTemp" :disabled="tempLocked" @update:model-value="(v) => (useTemp = Boolean(v))" />
            <span class="text-xs text-[#86909c]">{{
              tempLocked
                ? `鎖定 ${capabilities.lockedTemperatureValue}（Thinking 開啟）`
                : useTemp
                  ? '自訂'
                  : 'API 預設（gpt-5 系列鎖定）'
            }}</span>
            <a-slider
              v-if="useTemp && !tempLocked"
              :model-value="modelValue.temperature ?? 0"
              :min="0"
              :max="2"
              :step="0.1"
              class="w-[180px]"
              @update:model-value="(v) => patch({ temperature: v as number })"
            />
            <span v-if="useTemp">{{ tempLocked ? capabilities.lockedTemperatureValue : (modelValue.temperature ?? 0) }}</span>
          </a-space>
        </a-form-item>
      </a-col>
      <a-col v-if="capabilities.supportsThinking" :flex="'160px'">
        <a-form-item label="思考模式" content-flex label-col-flex="80px">
          <a-space>
            <a-switch
              :model-value="modelValue.thinking"
              checked-value="on"
              unchecked-value="off"
              @update:model-value="(v) => patch({ thinking: v as 'on' | 'off' })"
            />
            <span class="text-xs text-[#86909c]">{{ modelValue.thinking === 'on' ? '開啟' : '關閉' }}</span>
          </a-space>
        </a-form-item>
      </a-col>
    </a-row>

    <a-form-item v-if="capabilities.supportsThinking" label="Reasoning effort" content-flex label-col-flex="80px">
      <a-space>
        <a-radio-group
          :model-value="modelValue.reasoning_effort"
          type="button"
          size="small"
          :disabled="modelValue.thinking === 'off'"
          @update:model-value="(v) => patch({ reasoning_effort: String(v) as LlmReasoningEffort })"
        >
          <a-radio v-for="r in capabilities.reasoningEffortOptions" :key="r" :value="r">{{ r }}</a-radio>
        </a-radio-group>
        <span v-if="modelValue.thinking === 'off'" class="text-xs text-[#86909c]">
          Thinking 關閉：不送推理參數（不支援完全關閉的模型自動降為最低檔）
        </span>
      </a-space>
    </a-form-item>
  </div>
</template>

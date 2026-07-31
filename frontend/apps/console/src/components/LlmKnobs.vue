<script setup lang="ts">
import { computed, watch } from 'vue';
import { isNil } from 'lodash-es';
import {
  capabilitiesFor,
  MODEL_MIN_VERSION,
  PROVIDERS,
  REASONING,
} from '@/features/settings/constants';
import { modelMeetsMin } from '@/features/settings/utils';
import type { LlmKnobs, LlmReasoningEffort, LlmThinking } from '@/features/settings/types';

/** LLM 旋鈕組（model / thinking / reasoning_effort / temperature）：canonical 共用元件，
 * 供設定面板「功能區默認」與各功能區（prejudge/prompt_debug/sandbox）本次執行覆寫共用同一組控件與
 * 正規化邏輯，不得各自重做一套（見 .claude/rules/frontend-vue.md「同語義控件跨頁一致」）。
 * 2026-07-23 依三供應商官方文件全面重寫：OpenAI/Gemini 沒有獨立 thinking 開關（reasoning_effort
 * 本身即完整控制面，`capabilities.thinkingControl==='effortOnly'`），ByteDance/Ark 才有真實原生三態
 * thinking 開關（`thinkingControl==='nativeSwitch'`，見 capabilities.thinkingModes）——兩種供應商的
 * 控件形狀因此不同，由 `capabilities.thinkingControl` 分流渲染，取代舊版全供應商共用同一套假想
 * 「Thinking on/off + Reasoning effort」二段式控件。
 * 各家官方文件連結**不在此渲染**——那是 provider 級資訊（`providers[].docs`），每筆配置各印一次
 * 純屬重複，改由設定面板在供應商分頁上層渲染一次（見 LlmSettingsPanel）。 */
type Knobs = LlmKnobs;

const props = defineProps<{
  modelValue: Knobs;
  /** 決定 model 下拉清單與可配參數能力來源；由設定面板的模型配置編輯器帶入該配置的 provider。 */
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
// ⚠️ 值域取**該 model 的能力表**而非全域聯集：ByteDance provider 級只有 enabled/disabled，
// 只有 gpt-oss-120b-250805 額外支援 auto。用全域聯集會讓使用者對 seed-2-0-lite-* 選到 auto，
// 建出一筆名為 `thinking:auto` 但 Ark 會拒收的配置——名稱＝規格之後，這種名實不符尤其不能留。
const THINKING_CHOICES = computed(() => ['default', ...capabilities.value.thinkingModes]);

/** reasoning_effort 按鈕清單：只顯示「這個供應商底下至少一個 model 用得到」的值（provider 級預設、
 * 未套用個別 model 覆寫）——跨供應商本來就沒有的值（如 ByteDance 沒有 xhigh）直接不顯示，不是灰掉；
 * 同供應商內個別 model 進一步限縮的值（如 gpt-5-mini 不吃 none/xhigh）才用 disabled 灰掉，兩種情境視覺
 * 上分開處理，避免「永遠灰掉的按鈕」造成困惑。 */
const providerReasoningOptions = computed(
  () => PROVIDERS.find((p) => p.id === props.provider)?.reasoningEffortOptions ?? REASONING,
);
// ⚠️ 必須**聯集** provider 級與該 model 的能力表：per-model 覆寫可能**多出** provider 級沒有的檔位
// （實測 gemini-2.5-flash / gemini-3.5-flash 支援 `none`，但 gemini provider 級清單沒有它）——
// 只取 provider 級會讓那個檔位「能力表有、介面點不到」。依全域 REASONING 排序，保持版位穩定。
const REASONING_CHOICES = computed(() => {
  const usable = new Set([
    ...providerReasoningOptions.value,
    ...capabilities.value.reasoningEffortOptions,
  ]);
  return ['default', ...REASONING.filter((r) => usable.has(r))];
});

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

/** Reasoning effort 控件是否應 disable：僅 nativeSwitch 供應商在 thinking 為 disabled **或 auto**
 * 時成立——`client._reasoning_kwargs` 對這兩態一律不送 reasoning_effort（disabled 是官方確認的
 * 不可併送，auto 是查無官方資料時的保守處置）。effortOnly 供應商沒有這個概念，恆可選。 */
const reasoningEffortDisabled = computed(
  () =>
    capabilities.value.thinkingControl === 'nativeSwitch' &&
    (props.modelValue.thinking === 'disabled' || props.modelValue.thinking === 'auto'),
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

// 鎖定成立 → 清掉自訂 temperature（本配置不覆寫溫度、交給模型自己）。
// ⚠️ **刻意不帶 `immediate`**：配置名稱由規格衍生，immediate watcher 等於「使用者什麼都沒點、
// 一展開名字就變了」。`seed-2-0-lite-260228` 是 temperatureAlwaysLocked，展開它的瞬間就會被
// 改寫、名字多出一段，若剛好撞到既有規格，儲存鈕會 disabled 且**沒有任何操作能修**
// （watcher 會把值改回去）。拿掉之後只有使用者主動切 model/effort 進入鎖定態才觸發——可見、可逆。
// 清成 null 而非鎖定值：「鎖定」的語義是「這個配置不覆寫溫度」，不是「強制覆寫成 1」。
watch(tempLocked, (locked) => {
  if (locked && props.modelValue.temperature !== null) patch({ temperature: null });
});

// 既有自訂值超出新上限時夾回上限，避免送出該供應商 API 會拒絕的值。
// ⚠️ 三家目前 maxTemperature 皆為 2，故此 watcher 實際上不會觸發；保留作為「日後某家調降上限」
// 的防禦，不是當前有作用的邏輯（原註解宣稱「ByteDance 是 1」與 llm_model.json 不符，已更正）。
watch(
  () => capabilities.value.maxTemperature,
  (max) => {
    if (!tempLocked.value && useTemp.value && (props.modelValue.temperature ?? 0) > max) {
      patch({ temperature: max });
    }
  },
);

// 切換 model 可能改變 thinkingModes（如 gpt-oss-120b 有 auto、seed-2-0-lite-* 沒有）；
// 既有存值不在新清單時重置為 default（＝不送開關）。同樣不帶 `immediate`，理由見上。
watch(
  () => capabilities.value.thinkingModes,
  (modes) => {
    const cur = props.modelValue.thinking;
    if (cur && cur !== 'default' && !modes.includes(cur)) patch({ thinking: 'default' });
  },
);

// 切換 model/provider 可能改變 reasoningEffortOptions（如 ByteDance 官方值域無 xhigh，OpenAI 卻有）；
// 既有存值不在新選項清單時（殘留舊資料）重置為 medium——三家 reasoningEffortOptions 皆含此檔，
// 比退回「不送此參數」更明確可預期。
// ⚠️ 同樣**刻意不帶 `immediate`**（理由同上）：一展開就改值會讓衍生名在使用者沒操作的情況下變動。
watch(
  () => capabilities.value.reasoningEffortOptions,
  (options) => {
    const cur = props.modelValue.reasoning_effort;
    if (cur && cur !== 'default' && !options.includes(cur)) {
      patch({
        reasoning_effort: (options.includes('medium')
          ? 'medium'
          : options[0]) as LlmReasoningEffort,
      });
    }
  },
);
</script>

<template>
  <div class="flex flex-col gap-1">
    <a-form-item
      label="Model"
      content-flex
      label-col-flex="108px"
      :label-col-style="{ whiteSpace: 'nowrap' }"
    >
      <div class="flex flex-col gap-1">
        <a-select
          :model-value="modelValue.model"
          allow-create
          placeholder="從預設清單選（也可手動輸入臨時 model）"
          :trigger-props="{ autoFitPopupWidth: false, autoFitPopupMinWidth: true }"
          @update:model-value="(v) => patch({ model: String(v) })"
        >
          <a-option v-for="m in modelOptions" :key="m.id" :value="m.id" :label="m.id">
            <span>{{ m.id }}</span>
            <span v-if="m.desc" class="ml-2 whitespace-nowrap text-xs text-[#86909c]">{{
              m.desc
            }}</span>
          </a-option>
        </a-select>
        <span class="text-xs text-[#86909c]">{{
          selectedModelDesc || '清單外手動輸入的臨時 model，成本/用途未知'
        }}</span>
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
          <!-- 清單本身已由該 model 的能力表產生，不會出現需要灰掉的選項 -->
          <a-radio v-for="m in THINKING_CHOICES" :key="m" :value="m">{{ m }}</a-radio>
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

    <a-form-item
      label="Temperature"
      content-flex
      label-col-flex="108px"
      :label-col-style="{ whiteSpace: 'nowrap' }"
    >
      <div class="flex flex-col gap-1">
        <a-space :wrap="false" class="w-full">
          <a-switch
            :model-value="useTemp"
            :disabled="tempLocked"
            @update:model-value="(v) => (useTemp = Boolean(v))"
          />
          <a-slider
            v-if="useTemp && !tempLocked"
            :model-value="modelValue.temperature ?? 0"
            :min="0"
            :max="capabilities.maxTemperature"
            :step="0.1"
            class="w-[140px]"
            @update:model-value="(v) => patch({ temperature: v as number })"
          />
          <span v-if="useTemp && !tempLocked" class="whitespace-nowrap">{{
            modelValue.temperature ?? 0
          }}</span>
        </a-space>
        <!-- 鎖定時的文案刻意不再寫「鎖定 1」：我們並沒有送 1，而是**完全不送 temperature**、
             交由該 model 的 API 預設（送與不送對這些 model 行為相同，送了也會被忽略或拒絕）。 -->
        <span class="text-xs text-[#86909c]">{{
          tempLocked
            ? capabilities.temperatureAlwaysLocked
              ? '此 model 的 temperature 由伺服器固定，自訂值會被忽略——本配置不送此參數'
              : '推理生效中，官方 API 只接受預設溫度——本配置不送此參數'
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
            >{{ r }}</a-radio
          >
        </a-radio-group>
        <span class="text-xs text-[#86909c]">{{
          reasoningEffortDisabled
            ? modelValue.thinking === 'auto'
              ? '不可用：thinking=auto 由模型自行決定是否思考，不併送 reasoning_effort'
              : capabilities.reasoningOffHint || '不可用：目前狀態不支援送出 reasoning_effort'
            : modelValue.reasoning_effort && modelValue.reasoning_effort !== 'default'
              ? `將送出 reasoning_effort="${modelValue.reasoning_effort}"${modelValue.reasoning_effort === 'none' ? '（等同不啟用推理）' : ''}`
              : 'Default：不送此參數，使用該 model 的 API 預設值'
        }}</span>
      </div>
    </a-form-item>
  </div>
</template>

// 某功能區的 LLM 旋鈕 + 連線選擇（與「設定 › LLM 連線」同源 backend settings.llm_area_defaults）。
// 自 useLlmConfigs 改造（A schema）：不再是「選一套已存 config」，而是「跟隨該區團隊共用默認，
// 可本地臨時覆寫（不動預設），使用者可另按『存為此區默認』落庫」。
// 實作包 settingsConfigs Pinia store（單一真相）：設定抽屜的連線變更即時反映到各功能區；
// 各功能區本地覆寫預設不落庫，僅顯式「存為此區默認」才寫回 store/後端。
import { computed, reactive, ref, watch } from 'vue';
import { useSettingsConfigsStore } from '@/stores/settingsConfigs.store';
import type { LlmArea, LlmAreaDefault } from '@/features/settings/types';

type Knobs = Pick<LlmAreaDefault, 'model' | 'thinking' | 'reasoning_effort' | 'temperature'>;

const BLANK_KNOBS: Knobs = {
  model: '',
  thinking: 'default',
  reasoning_effort: 'default',
  temperature: null,
};

/**
 * 某功能區（prejudge/prompt_debug/sandbox）的 LLM 連線 + 旋鈕狀態。
 * @param area 功能區 key。
 * @returns `provider`（v-model 綁 LlmConfigPicker）、`knobs`（v-model 綁 LlmKnobs）、
 *   `overrides`（本次執行送出用，provider+knobs 合一）、`providerHasToken`（連線狀態點）、
 *   `loadConfigs`（載入，失敗不阻斷）、`saveAsDefault`（把目前 provider+knobs 存為此區團隊共用默認）、
 *   `dirty`（本地是否已偏離團隊默認）。
 */
export function useLlmAreaDefault(area: LlmArea) {
  const store = useSettingsConfigsStore();

  const provider = ref('openai');
  const knobs = reactive<Knobs>({ ...BLANK_KNOBS });
  /** 使用者是否已本地手動改動過（改動後不再被團隊默認的後續變動靜默覆蓋，避免蓋掉進行中的編輯）。 */
  const dirty = ref(false);

  watch(
    () => store.llmAreaDefaults[area],
    (def) => {
      if (dirty.value || !def) return;
      provider.value = def.provider;
      Object.assign(knobs, {
        model: def.model,
        thinking: def.thinking,
        reasoning_effort: def.reasoning_effort,
        temperature: def.temperature,
      });
    },
    { immediate: true },
  );

  const loadConfigs = async (): Promise<void> => {
    try {
      await store.loadAll();
    } catch {
      /* 載入失敗維持空狀態，不阻斷頁面 */
    }
  };

  /** LlmConfigPicker 的 update:modelValue handler：切換本次用哪個供應商連線。 */
  const setProvider = (p: string): void => {
    dirty.value = true;
    provider.value = p;
  };
  /** LlmKnobs 的 update:modelValue handler。 */
  const setKnobs = (next: Knobs): void => {
    dirty.value = true;
    Object.assign(knobs, next);
  };

  /** 本次執行送出用的 overrides（provider + 旋鈕）；三功能區的 startXxx 呼叫皆用此組 overrides。 */
  const overrides = computed(() => ({ provider: provider.value, ...knobs }));

  /** 把目前 provider + knobs 存為此區團隊共用默認。 */
  const saveAsDefault = async (): Promise<void> => {
    await store.saveLlmAreaDefault(area, { provider: provider.value, ...knobs });
    dirty.value = false;
  };

  const providerHasToken = computed(() => store.providerHasToken);

  return {
    provider,
    knobs,
    overrides,
    providerHasToken,
    loadConfigs,
    setProvider,
    setKnobs,
    saveAsDefault,
    dirty,
  };
}

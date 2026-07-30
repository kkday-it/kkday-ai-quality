// 某功能區的 LLM 旋鈕 + 連線選擇（與「設定 › LLM 連線」同源 backend settings.llm_area_defaults）。
// 自 useLlmConfigs 改造（A schema）：不再是「選一套已存 config」，而是「跟隨該區團隊共用默認，
// 可本地臨時覆寫（不動預設），使用者可另按『存為此區默認』落庫」。
// 實作包 settingsConfigs Pinia store（單一真相）：設定抽屜的連線變更即時反映到各功能區；
// 各功能區本地覆寫預設不落庫，僅顯式「存為此區默認」才寫回 store/後端。
import { computed, reactive, ref, watch } from 'vue';
import { useSettingsConfigsStore } from '@/stores/settingsConfigs.store';
import { defaultModelFor, LLM_AREA_SEEDS } from '@/features/settings/constants';
import type { LlmArea, LlmKnobs } from '@/features/settings/types';

type Knobs = LlmKnobs;

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
  // 起點＝該區在 config 登記的出廠預設（如 prompt_revise 直接起在旗艦模型 + high effort）；
  // 未登記的區維持全空白，行為與此機制加入前一致。團隊默認一載回來就覆蓋（下方 watch）。
  const knobs = reactive<Knobs>({ ...BLANK_KNOBS, ...(LLM_AREA_SEEDS[area] ?? {}) });
  /** 使用者是否已本地手動改動過（改動後不再被團隊默認的後續變動靜默覆蓋，避免蓋掉進行中的編輯）。 */
  const dirty = ref(false);

  /** 某供應商在本區已存的旋鈕；沒存過 → 該家的起點（model 用該供應商自己的預設，不沿用前一家的）。 */
  const knobsForProvider = (p: string): Knobs => {
    const saved = store.llmAreaDefaults[area]?.knobs?.[p];
    if (saved) return { ...BLANK_KNOBS, ...saved };
    return { ...BLANK_KNOBS, ...(LLM_AREA_SEEDS[area] ?? {}), model: defaultModelFor(p) };
  };

  watch(
    () => store.llmAreaDefaults[area],
    (def) => {
      if (dirty.value || !def) return;
      provider.value = def.provider;
      Object.assign(knobs, knobsForProvider(def.provider));
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

  /** LlmConfigPicker 的 update:modelValue handler：切換本次用哪個供應商連線。
   *
   * **整組旋鈕一起換成該供應商自己的**，不是只換 model。舊實作只重置 model，於是
   * thinking / reasoning_effort / temperature 會殘留前一家的值——使用者以為在配新供應商，
   * 實際上部分旋鈕還是舊的（各家值域與鎖定規則不同，殘留值可能根本不適用）。 */
  const setProvider = (p: string): void => {
    dirty.value = true;
    provider.value = p;
    Object.assign(knobs, knobsForProvider(p));
  };
  /** LlmKnobs 的 update:modelValue handler。 */
  const setKnobs = (next: Knobs): void => {
    dirty.value = true;
    Object.assign(knobs, next);
  };

  /** 本次執行送出用的 overrides（provider + 旋鈕）；三功能區的 startXxx 呼叫皆用此組 overrides。 */
  const overrides = computed(() => ({ provider: provider.value, ...knobs }));

  /** 把目前 provider + 旋鈕存為此區團隊共用默認（**只寫當前供應商那一份**，不動另外兩家）。 */
  const saveAsDefault = async (): Promise<void> => {
    await store.saveLlmAreaDefault(area, provider.value, { ...knobs });
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

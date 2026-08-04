// 某功能區（prejudge/prompt_debug/prompt_revise）用哪一筆具名模型配置。
//
// 配置**內容**與**綁定**都是團隊共享資產，同存 DB `settings`（配置內容在「設定 › LLM 設定」編輯）。
//
// ⚠️ **綁定是團隊共用的單一份，不是個人設定**（2026-07-31 使用者拍板）：一個人在功能區換了配置，
// 同事下次進頁面就會看到新的。這是**刻意的、不是 bug**——中途曾改存 localStorage 求「個人選擇
// 互不干擾」，但瀏覽器儲存跨不了人也跨不了裝置，同事與新電腦永遠拿不到你調好的安排，違背
// 「別人能直接用到我提交的配置」的實際需求，故收回 DB。要恢復 per-user 隔離必須先有 per-user
// 設定層，不是把它移回瀏覽器。
//
// **選了就存**：功能區的配置下拉沒有儲存按鈕，`configId` 一被寫入就落庫（樂觀更新 + 失敗回滾，
// 見 store 的 `saveLlmAreaConfig`）。也因為每個使用點都自動同步，設定面板不另設集中綁定區塊——
// 那會變成同一件事的第二個入口（配置手風琴上已標示每筆配置「用於」哪些功能區）。
//
// 功能區頁面上只有這一個下拉，不持有任何旋鈕編輯狀態——要改旋鈕請去設定面板改配置本身，
// 改完所有指向它的功能區同步生效。
import { computed } from 'vue';
import { Message } from '@arco-design/web-vue';
import { useSettingsConfigsStore } from '@/stores/settingsConfigs.store';
import { LLM_AREAS, LLM_AREA_DEFAULT_CONFIG_IDS } from '@/features/settings/constants';
import type { LlmArea, LlmModelConfig } from '@/features/settings/types';

/**
 * 哪些功能區正指向這筆配置（設定面板刪除前的提示用）。
 *
 * 綁定在 DB，所以這是**跨使用者的真實引用**——刪掉會影響到所有人，不只自己這台。
 *
 * @param areaConfigs store 的 `llmAreaConfigs`（area → config id）。
 * @param configId 要查的配置 id。
 * @returns 正在使用它的功能區 key 清單；沒有則空陣列。
 */
export function areasUsingConfig(areaConfigs: Record<string, string>, configId: string): LlmArea[] {
  if (!configId) return [];
  return (LLM_AREAS as LlmArea[]).filter((a) => areaConfigs[a] === configId);
}

/**
 * 某功能區的模型配置選擇。
 *
 * @param area 功能區 key。
 * @returns `configId`（v-model 綁配置下拉，**寫入即落庫**）、`configs`（可選清單）、
 *   `config`（當前生效的那筆，供顯示名稱／送 config_name）、`overrides`（本次執行送出用，
 *   **形狀與改造前完全相同**，故消費端與後端契約皆零改動）、`ready`（清單是否已載入，
 *   消費端據此 gate 執行按鈕）、`providerHasToken`（下拉選項的連線狀態點）、
 *   `loadConfigs`（載入，失敗不阻斷）。
 */
export function useLlmAreaConfig(area: LlmArea) {
  const store = useSettingsConfigsStore();

  /** 生效清單（單層，與後端 `settings.all_model_configs()` 同源）。 */
  const configs = computed<LlmModelConfig[]>(() => store.llmModelConfigs);

  /**
   * 配置清單是否已就緒。
   *
   * **消費端必須用它 gate 執行按鈕**：`loadAll()` 回來前清單是空的，`config` 會是 undefined、
   * `overrides` 全是空字串，而後端 `effective_llm_dict` 對空 model 會 `or _DEFAULT_LLM["model"]`
   * ——結果是**靜默用全域預設 model 跑掉一整批**。
   */
  const ready = computed(() => configs.value.length > 0);

  /** DB 裡登記的綁定（沒綁過就是空字串，交給下面回落）。 */
  const boundId = computed<string>(() => store.llmAreaConfigs[area] ?? '');

  /**
   * 當前生效的配置。三級回落：DB 綁定 → 該區出廠預設 → 清單第一筆。
   *
   * 回落是必要的而非防禦性冗餘：後端刪配置時雖會剪除指向它的綁定，但「該區從沒綁過」是常態
   * （全新環境、新加的功能區），那時第二級才是實際依據。
   */
  const config = computed<LlmModelConfig | undefined>(() => {
    const list = configs.value;
    return (
      list.find((c) => c.id === boundId.value) ??
      list.find((c) => c.id === LLM_AREA_DEFAULT_CONFIG_IDS[area]) ??
      list[0]
    );
  });

  /**
   * 配置下拉的 v-model。
   *
   * get 刻意回**生效那筆**的 id 而非 DB 原值：兩者只在「綁定指向的配置已不存在」時不同，
   * 那時顯示生效值才對得上實際行為（顯示原值會讓下拉整個空白，看起來像壞掉）。
   * set 直接落庫——這裡沒有儲存按鈕，選擇即是提交。
   */
  const configId = computed<string>({
    get: () => config.value?.id ?? '',
    set: (next) => {
      if (!next || next === boundId.value) return;
      store.saveLlmAreaConfig(area, next).catch((e: unknown) => {
        Message.error((e as Error)?.message || '儲存功能區預設配置失敗');
      });
    },
  });

  /**
   * 本次執行送出用的 overrides（provider + 旋鈕攤平）。
   *
   * **形狀刻意與改造前一字不差**——後端 `effective_llm_dict` 只吃 flat 旋鈕、不認識「配置」這個
   * 抽象，所以配置庫這個新概念完全不必滲進 judge 路徑，四條既有端點零改動。
   */
  const overrides = computed(() => ({
    provider: config.value?.provider ?? '',
    model: config.value?.model ?? '',
    thinking: config.value?.thinking ?? ('default' as const),
    reasoning_effort: config.value?.reasoning_effort ?? ('default' as const),
    temperature: config.value?.temperature ?? null,
  }));

  const loadConfigs = async (): Promise<void> => {
    try {
      await store.loadAll();
    } catch {
      /* 載入失敗不阻斷頁面；消費端以 ready 擋住執行 */
    }
  };

  const providerHasToken = computed(() => store.providerHasToken);

  return { configId, configs, config, ready, overrides, providerHasToken, loadConfigs };
}

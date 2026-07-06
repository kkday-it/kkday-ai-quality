// 已保存 LLM 模型配置的載入與選中（與「設定 › LLM 模型連線」同源 backend settings.llm_configs）。
// 自 useAttributionList 下沉為獨立 composable，使主 composable 專注列表/篩選/判決編排。
// 實作改包 settingsConfigs Pinia store（單一真相）：設定抽屜的增刪/啟用切換即時反映到歸因頁，
// 歸因頁工具列切換亦持久化回 settings（雙向同步，取代原本一次性 getSettings 快照造成的漂移）。
import { computed, ref, watch } from 'vue';
import { useSettingsConfigsStore } from '@/stores/settingsConfigs.store';

/** LLM 模型配置選項（同「設定 › LLM 模型連線」）。 */
export interface LlmConfigOpt {
  id: string;
  provider: string;
  model: string;
  reasoning_effort: string;
}

/**
 * 已保存 LLM 配置清單 + 選中（跟隨全域啟用中，可臨時覆寫）+ 全域啟用切換。
 * @returns `llmConfigId`（本次判決選用 id，預設跟隨啟用中）、`llmConfigs`（選項清單，隨 store 即時更新）、
 *   `activeLlmId`（全域啟用中 id）、`loadConfigs`（載入，失敗回空不阻斷）、`setActiveLlm`（切換全域啟用並持久化）
 */
export function useLlmConfigs() {
  const store = useSettingsConfigsStore();
  const llmConfigId = ref('');
  const llmConfigs = computed<LlmConfigOpt[]>(() =>
    store.llmConfigs.map((c) => ({
      id: c.id,
      provider: c.provider || '',
      model: c.model || '',
      reasoning_effort: c.reasoning_effort || 'default',
    }))
  );
  const activeLlmId = computed(() => store.activeLlmId || '');
  // 選中恆跟隨全域啟用中（設定抽屜或工具列切換都會帶動）；modal 內臨時改選僅影響該次送出
  watch(
    activeLlmId,
    (id) => {
      llmConfigId.value = id || llmConfigs.value[0]?.id || '';
    },
    { immediate: true }
  );
  const loadConfigs = async (): Promise<void> => {
    try {
      await store.loadAll();
    } catch {
      /* 載入失敗維持空清單，不阻斷列表頁 */
    }
  };
  /** 切換全域啟用中配置（持久化 active_llm_config_id；與設定抽屜同一寫入路徑）。 */
  const setActiveLlm = (id: string): Promise<void> => store.setActiveLlm(id);
  return { llmConfigId, llmConfigs, activeLlmId, loadConfigs, setActiveLlm };
}

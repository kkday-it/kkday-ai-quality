// 已保存 LLM 模型配置的載入與選中（與「設定 › LLM 模型連線」同源 backend settings.llm_configs）。
// 自 useAttributionList 下沉為獨立 composable，使主 composable 專注列表/篩選/判決編排。
import { ref } from 'vue';
import { getSettings } from '@/api';

/** LLM 模型配置選項（同「設定 › LLM 模型連線」）。 */
export interface LlmConfigOpt {
  id: string;
  provider: string;
  model: string;
  reasoning_effort: string;
}

/**
 * 載入已保存 LLM 配置清單 + 選中 active。
 * @returns `llmConfigId`（選中 id）、`llmConfigs`（選項清單）、`loadConfigs`（載入，失敗回空不阻斷）
 */
export function useLlmConfigs() {
  const llmConfigId = ref('');
  const llmConfigs = ref<LlmConfigOpt[]>([]);
  const loadConfigs = async (): Promise<void> => {
    try {
      const s = await getSettings();
      llmConfigs.value = (s.llm_configs || []).map((c: any) => ({
        id: c.id,
        provider: c.provider || '',
        model: c.model || '',
        reasoning_effort: c.reasoning_effort || 'default',
      }));
      llmConfigId.value = s.active_llm_config_id || llmConfigs.value[0]?.id || '';
    } catch {
      llmConfigs.value = [];
    }
  };
  return { llmConfigId, llmConfigs, loadConfigs };
}

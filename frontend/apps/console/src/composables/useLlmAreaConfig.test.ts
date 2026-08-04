// useLlmAreaConfig：功能區 → 模型配置的綁定（存 DB，團隊共用單一份）。
//
// 鎖四件容易在重構中被破壞的事：
//   ① 選了就存——沒有儲存按鈕，寫入 configId 即呼叫 saveLlmAreaConfig（漏掉就變成「選了沒生效」）
//   ② 綁定指向的配置不存在時**回落**而非壞掉（同事刪了配置、或該區從沒綁過）
//   ③ 清單未載入時 `ready` 為 false（否則消費端會拿著空 model 送出，後端靜默用全域預設跑掉一整批）
//   ④ overrides 形狀不變（後端 effective_llm_dict 契約零改動的前提）
import { describe, expect, it, beforeEach, vi } from 'vitest';
import { ref } from 'vue';
import { LLM_AREA_DEFAULT_CONFIG_IDS } from '@/features/settings/constants';
import type { LlmModelConfig } from '@/features/settings/types';

const llmModelConfigs = ref<LlmModelConfig[]>([]);
const llmAreaConfigs = ref<Record<string, string>>({});
const providerHasToken = ref<Record<string, boolean>>({});
const loadAll = vi.fn().mockResolvedValue(undefined);
/** 模擬 store 的樂觀更新：先改本地再落庫（真實實作見 settingsConfigs.store）。 */
const saveLlmAreaConfig = vi.fn(async (area: string, configId: string) => {
  llmAreaConfigs.value = { ...llmAreaConfigs.value, [area]: configId };
});
const messageError = vi.fn();

vi.mock('@arco-design/web-vue', () => ({ Message: { error: (m: string) => messageError(m) } }));

vi.mock('@/stores/settingsConfigs.store', () => ({
  useSettingsConfigsStore: () => ({
    get llmModelConfigs() {
      return llmModelConfigs.value;
    },
    get llmAreaConfigs() {
      return llmAreaConfigs.value;
    },
    get providerHasToken() {
      return providerHasToken.value;
    },
    loadAll,
    saveLlmAreaConfig,
  }),
}));

import { areasUsingConfig, useLlmAreaConfig } from './useLlmAreaConfig';

/** 後端 `all_model_configs()` 會補上衍生的 name；store 拿到的就是這個形狀。 */
const cfg = (over: Partial<LlmModelConfig> = {}): LlmModelConfig => ({
  id: 'cfg-custom',
  name: 'ByteDance · seed-2-0-lite-260428 · thinking:enabled · low',
  provider: 'bytedance',
  model: 'seed-2-0-lite-260428',
  thinking: 'enabled',
  reasoning_effort: 'low',
  temperature: null,
  ...over,
});

/** 模擬後端回來的配置清單（含各功能區的出廠起點）。 */
const defaults = (): LlmModelConfig[] => [
  {
    id: LLM_AREA_DEFAULT_CONFIG_IDS.prejudge,
    name: 'OpenAI · gpt-5.4-mini · medium',
    provider: 'openai',
    model: 'gpt-5.4-mini',
    thinking: 'default',
    reasoning_effort: 'medium',
    temperature: null,
  },
  {
    id: LLM_AREA_DEFAULT_CONFIG_IDS.prompt_revise,
    name: 'OpenAI · gpt-5.5 · high',
    provider: 'openai',
    model: 'gpt-5.5',
    thinking: 'default',
    reasoning_effort: 'high',
    temperature: null,
  },
];

describe('useLlmAreaConfig', () => {
  beforeEach(() => {
    llmModelConfigs.value = defaults();
    llmAreaConfigs.value = {};
    providerHasToken.value = {};
    loadAll.mockClear();
    saveLlmAreaConfig.mockClear();
    messageError.mockClear();
  });

  it('清單未載入時 ready 為 false（消費端據此 gate 執行按鈕）', () => {
    llmModelConfigs.value = [];
    const { ready, config } = useLlmAreaConfig('prejudge');
    expect(ready.value).toBe(false);
    expect(config.value).toBeUndefined();

    llmModelConfigs.value = defaults();
    expect(ready.value).toBe(true);
    expect(config.value).toBeDefined();
  });

  it('清單為單層，不再前端合併任何常數', () => {
    llmModelConfigs.value = [...defaults(), cfg()];
    const { configs } = useLlmAreaConfig('prejudge');
    expect(configs.value.map((c) => c.id)).toEqual([
      LLM_AREA_DEFAULT_CONFIG_IDS.prejudge,
      LLM_AREA_DEFAULT_CONFIG_IDS.prompt_revise,
      'cfg-custom',
    ]);
  });

  it('未綁過 → 用該區的出廠預設起點', () => {
    expect(useLlmAreaConfig('prompt_revise').config.value?.id).toBe(
      LLM_AREA_DEFAULT_CONFIG_IDS.prompt_revise,
    );
  });

  it('各區出廠起點可以不同（跑批要便宜、改 Prompt 要聰明）', () => {
    expect(LLM_AREA_DEFAULT_CONFIG_IDS.prejudge).not.toBe(
      LLM_AREA_DEFAULT_CONFIG_IDS.prompt_revise,
    );
    expect(useLlmAreaConfig('prejudge').config.value?.id).toBe(
      LLM_AREA_DEFAULT_CONFIG_IDS.prejudge,
    );
  });

  it('選了就存：寫入 configId 直接落庫，不需要另按儲存', () => {
    llmModelConfigs.value = [...defaults(), cfg()];
    const { configId, config } = useLlmAreaConfig('prejudge');

    configId.value = 'cfg-custom';

    expect(saveLlmAreaConfig).toHaveBeenCalledWith('prejudge', 'cfg-custom');
    expect(config.value?.model).toBe('seed-2-0-lite-260428');
  });

  it('選到同一筆不重複落庫（避免每次渲染/回填都打一次後端）', () => {
    llmModelConfigs.value = [...defaults(), cfg()];
    llmAreaConfigs.value = { prejudge: 'cfg-custom' };
    const { configId } = useLlmAreaConfig('prejudge');

    configId.value = 'cfg-custom';

    expect(saveLlmAreaConfig).not.toHaveBeenCalled();
  });

  it('落庫失敗 → 提示使用者（store 已回滾，畫面不得停在假生效狀態）', async () => {
    llmModelConfigs.value = [...defaults(), cfg()];
    saveLlmAreaConfig.mockRejectedValueOnce(new Error('功能區「prejudge」指向的模型配置不存在'));
    const { configId } = useLlmAreaConfig('prejudge');

    configId.value = 'cfg-custom';
    await Promise.resolve();

    expect(messageError).toHaveBeenCalledWith('功能區「prejudge」指向的模型配置不存在');
  });

  it('綁定是團隊共用的一份：一區改了，其他區維持自己的綁定', () => {
    llmModelConfigs.value = [...defaults(), cfg()];
    useLlmAreaConfig('prejudge').configId.value = 'cfg-custom';

    expect(llmAreaConfigs.value.prompt_revise).toBeUndefined();
    expect(useLlmAreaConfig('prompt_revise').config.value?.id).toBe(
      LLM_AREA_DEFAULT_CONFIG_IDS.prompt_revise,
    );
  });

  it('綁定指向的配置被刪除 → 回落該區出廠起點，不是壞掉', () => {
    llmModelConfigs.value = [...defaults(), cfg()];
    llmAreaConfigs.value = { prejudge: 'cfg-custom' };
    const { config } = useLlmAreaConfig('prejudge');
    expect(config.value?.id).toBe('cfg-custom');

    llmModelConfigs.value = defaults(); // 同事在設定面板刪掉了這筆

    expect(config.value?.id).toBe(LLM_AREA_DEFAULT_CONFIG_IDS.prejudge);
  });

  it('configId 顯示的是生效那筆，不是 DB 原值（否則下拉會整個空白）', () => {
    llmAreaConfigs.value = { prejudge: 'cfg-已被刪掉' };
    const { configId } = useLlmAreaConfig('prejudge');

    expect(configId.value).toBe(LLM_AREA_DEFAULT_CONFIG_IDS.prejudge);
  });

  it('overrides 形狀與改造前一字不差（後端 effective_llm_dict 契約零改動的前提）', () => {
    llmModelConfigs.value = [...defaults(), cfg({ temperature: 0.7 })];
    llmAreaConfigs.value = { prejudge: 'cfg-custom' };
    const { overrides } = useLlmAreaConfig('prejudge');

    expect(Object.keys(overrides.value).sort()).toEqual([
      'model',
      'provider',
      'reasoning_effort',
      'temperature',
      'thinking',
    ]);
    expect(overrides.value).toEqual({
      provider: 'bytedance',
      model: 'seed-2-0-lite-260428',
      thinking: 'enabled',
      reasoning_effort: 'low',
      temperature: 0.7,
    });
    // 配置身分（id/name）不得滲進 judge 契約——名稱衍生化後這條更重要：name 是顯示投影，
    // 送進 overrides 會讓後端誤以為那是個旋鈕。
    expect(overrides.value).not.toHaveProperty('id');
    expect(overrides.value).not.toHaveProperty('name');
  });
});

describe('areasUsingConfig', () => {
  it('回報跨使用者的真實引用（綁定在 DB，不是只看自己這台）', () => {
    expect(areasUsingConfig({ prejudge: 'cfg-a', prompt_debug: 'cfg-b' }, 'cfg-a')).toEqual([
      'prejudge',
    ]);
    expect(areasUsingConfig({ prejudge: 'cfg-a', prompt_debug: 'cfg-a' }, 'cfg-a')).toEqual([
      'prejudge',
      'prompt_debug',
    ]);
    expect(areasUsingConfig({ prejudge: 'cfg-a' }, 'cfg-zzz')).toEqual([]);
    expect(areasUsingConfig({ prejudge: 'cfg-a' }, '')).toEqual([]);
  });
});

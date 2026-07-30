// per-provider 旋鈕的回歸鎖：切換供應商必須帶出「那一家自己的」設定，而非殘留前一家的值。
// 對應 2026-07-30 修掉的兩個缺陷：
//   ③ llm_area_defaults 只裝得下一組旋鈕 → 三個供應商 tab 互相覆蓋
//   ④ setProvider 只重置 model → thinking/reasoning_effort/temperature 殘留跨供應商污染
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ref } from 'vue';

const saveLlmAreaDefaultMock = vi.fn();
const llmAreaDefaults = ref<Record<string, unknown>>({});

vi.mock('@/stores/settingsConfigs.store', () => ({
  useSettingsConfigsStore: () => ({
    llmAreaDefaults: llmAreaDefaults.value,
    providerHasToken: {},
    loadAll: vi.fn(),
    saveLlmAreaDefault: saveLlmAreaDefaultMock,
  }),
}));

import { useLlmAreaDefault } from './useLlmAreaDefault';

describe('useLlmAreaDefault：per-provider 旋鈕', () => {
  beforeEach(() => {
    saveLlmAreaDefaultMock.mockReset();
    llmAreaDefaults.value = {
      prejudge: {
        provider: 'openai',
        knobs: {
          openai: {
            model: 'gpt-5.4-mini',
            thinking: 'enabled',
            reasoning_effort: 'high',
            temperature: 1,
          },
          bytedance: {
            model: 'seed-2-0-lite-260428',
            thinking: 'disabled',
            reasoning_effort: 'low',
            temperature: 0.7,
          },
        },
      },
    };
  });

  it('載入時取「當前選定供應商」那一組旋鈕', () => {
    const { provider, knobs } = useLlmAreaDefault('prejudge');
    expect(provider.value).toBe('openai');
    expect(knobs.model).toBe('gpt-5.4-mini');
    expect(knobs.reasoning_effort).toBe('high');
  });

  it('切換供應商 → 整組旋鈕換成該家自己存的，不殘留前一家的值（缺陷④）', () => {
    const { setProvider, provider, knobs } = useLlmAreaDefault('prejudge');
    setProvider('bytedance');

    expect(provider.value).toBe('bytedance');
    expect(knobs.model).toBe('seed-2-0-lite-260428');
    // 這三個是舊實作不會重置、會殘留 openai 值的欄位
    expect(knobs.thinking).toBe('disabled');
    expect(knobs.reasoning_effort).toBe('low');
    expect(knobs.temperature).toBe(0.7);
  });

  it('切到「沒存過旋鈕」的供應商 → model 用該家預設，不沿用前一家的 model id', () => {
    const { setProvider, knobs } = useLlmAreaDefault('prejudge');
    setProvider('gemini');

    expect(knobs.model).not.toBe('gpt-5.4-mini');
    expect(knobs.model).not.toBe('');
  });

  it('存為此區默認只寫當前供應商那一份（不整包送，否則後端會沖掉其他家）', async () => {
    const { setProvider, saveAsDefault } = useLlmAreaDefault('prejudge');
    setProvider('bytedance');
    await saveAsDefault();

    expect(saveLlmAreaDefaultMock).toHaveBeenCalledTimes(1);
    const [area, provider, knobs] = saveLlmAreaDefaultMock.mock.calls[0];
    expect(area).toBe('prejudge');
    expect(provider).toBe('bytedance');
    expect(knobs).toMatchObject({ model: 'seed-2-0-lite-260428' });
    // 送出的是「單一供應商的旋鈕」，不含 provider 欄位、也不含其他家
    expect(knobs).not.toHaveProperty('provider');
    expect(knobs).not.toHaveProperty('openai');
  });
});

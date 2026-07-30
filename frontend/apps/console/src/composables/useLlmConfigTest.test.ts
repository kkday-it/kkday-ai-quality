// 缺陷⑧ 的回歸鎖：測試連線結果只對「當時那組配置」有效，配置一改就必須失效——
// 否則使用者切了供應商／改了旋鈕，畫面仍顯示上一組配置的「連線成功」，誤以為現在這組也驗證過了。
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { nextTick, reactive, ref } from 'vue';

import type { LlmKnobs } from '@/features/settings/types';

vi.mock('@arco-design/web-vue', () => ({
  Message: { success: vi.fn(), error: vi.fn() },
}));
vi.mock('@/api', () => ({ testLlm: vi.fn() }));

import { testLlm } from '@/api';
import { useLlmConfigTest } from './useLlmConfigTest';

const testLlmMock = vi.mocked(testLlm);

describe('useLlmConfigTest：配置變動即失效', () => {
  const provider = ref('openai');
  const knobs = reactive<LlmKnobs>({
    model: 'gpt-5.4-mini',
    thinking: 'enabled',
    reasoning_effort: 'high',
    temperature: 1,
  });

  beforeEach(() => {
    testLlmMock.mockReset();
    testLlmMock.mockResolvedValue({ ok: true });
    provider.value = 'openai';
    Object.assign(knobs, {
      model: 'gpt-5.4-mini',
      thinking: 'enabled',
      reasoning_effort: 'high',
      temperature: 1,
    });
  });

  it('測試成功後保留結果', async () => {
    const { testResult, onTest } = useLlmConfigTest(
      () => provider.value,
      () => knobs,
    );
    await onTest();
    expect(testResult.value).toEqual({ ok: true });
  });

  it('切換供應商 → 結果清空（不再顯示上一組配置的成功）', async () => {
    const { testResult, onTest } = useLlmConfigTest(
      () => provider.value,
      () => knobs,
    );
    await onTest();
    expect(testResult.value).not.toBeNull();

    provider.value = 'bytedance';
    await nextTick();
    expect(testResult.value).toBeNull();
  });

  it('改旋鈕（不換供應商）→ 結果同樣清空', async () => {
    const { testResult, onTest } = useLlmConfigTest(
      () => provider.value,
      () => knobs,
    );
    await onTest();
    expect(testResult.value).not.toBeNull();

    knobs.reasoning_effort = 'low';
    await nextTick();
    expect(testResult.value).toBeNull();
  });
});

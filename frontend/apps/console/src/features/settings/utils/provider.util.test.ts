import { describe, expect, it } from 'vitest';
import { deriveProviderId, modelMeetsMin } from './provider.util';

describe('deriveProviderId', () => {
  it('由 base_url 子字串反推非官方 provider', () => {
    expect(deriveProviderId('https://generativelanguage.googleapis.com/v1beta')).toBe('gemini');
    expect(deriveProviderId('https://ark.bytepluses.com/api/v3')).toBe('bytedance');
  });

  it('無法辨識時退回 openai（安全預設）', () => {
    expect(deriveProviderId('')).toBe('openai');
    expect(deriveProviderId('https://unknown.example/v1')).toBe('openai');
  });
});

describe('modelMeetsMin', () => {
  it('gpt-* 依主.次版本比較門檻', () => {
    expect(modelMeetsMin('gpt-5.4-mini', '5.4')).toBe(true); // 等於門檻
    expect(modelMeetsMin('gpt-5.5', '5.4')).toBe(true); // 次版本高
    expect(modelMeetsMin('gpt-6.0', '5.4')).toBe(true); // 主版本高
    expect(modelMeetsMin('gpt-5.0', '5.4')).toBe(false); // 次版本低
    expect(modelMeetsMin('gpt-4.9', '5.4')).toBe(false); // 主版本低
  });

  it('gpt 無次版本視為 .0', () => {
    expect(modelMeetsMin('gpt-5', '5.4')).toBe(false); // 5.0 < 5.4
    expect(modelMeetsMin('gpt-5', '5')).toBe(true); // 5.0 >= 5.0
  });

  it('非 gpt-* model 一律放行（不受版本規則誤濾）', () => {
    expect(modelMeetsMin('gemini-1.5-pro', '5.4')).toBe(true);
    expect(modelMeetsMin('doubao-pro', '5.4')).toBe(true);
    expect(modelMeetsMin('', '5.4')).toBe(true);
  });
});

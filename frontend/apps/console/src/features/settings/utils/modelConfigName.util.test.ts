// 衍生配置名的前後端 parity 鎖。
//
// 下方黃金清單與 `backend/tests/test_settings.py::test_derive_config_name_golden` **逐字相同**，
// 任一端改了折疊規則或版面就會有一邊轉紅。名稱是使用者辨識配置的唯一依據、也是跑批 manifest
// 的追溯欄位，前後端漂移會讓「同一筆配置在設定面板叫 A、在跑批紀錄叫 B」。
import { describe, expect, it } from 'vitest';
import { deriveConfigName, specKey, specKeyOf } from './modelConfigName.util';
import type { LlmModelConfig } from '../types';

const c = (over: Partial<LlmModelConfig>): Partial<LlmModelConfig> => ({
  thinking: 'default',
  reasoning_effort: 'default',
  temperature: null,
  ...over,
});

/** 黃金清單：4 筆預設配置 + dev 實際 5 筆自訂（含 R1/R2 折疊案例）。 */
const GOLDEN: Array<[Partial<LlmModelConfig>, string]> = [
  [
    c({ provider: 'openai', model: 'gpt-5.4-mini', reasoning_effort: 'medium' }),
    'OpenAI · gpt-5.4-mini · medium',
  ],
  [
    c({ provider: 'openai', model: 'gpt-5.5', reasoning_effort: 'high' }),
    'OpenAI · gpt-5.5 · high',
  ],
  [
    c({ provider: 'gemini', model: 'gemini-3.5-flash', reasoning_effort: 'medium' }),
    'Gemini · gemini-3.5-flash · medium',
  ],
  [
    c({ provider: 'bytedance', model: 'seed-2-0-lite-260228', reasoning_effort: 'medium' }),
    'ByteDance · seed-2-0-lite-260228 · medium',
  ],
  [
    c({
      provider: 'bytedance',
      model: 'seed-2-0-lite-260428',
      thinking: 'enabled',
      reasoning_effort: 'high',
    }),
    'ByteDance · seed-2-0-lite-260428 · thinking:enabled · high',
  ],
  // ⬇️ thinking 被 R1 折掉、temperature 被 R3 折掉（該組合 API 只接受預設值）→ 與第 1 筆同一個
  //    配置，故不重複列出（見下方 fold 專屬測試）
  [
    c({
      provider: 'bytedance',
      model: 'seed-2-0-lite-260428',
      thinking: 'enabled',
      reasoning_effort: 'medium',
      temperature: 1.0,
    }),
    'ByteDance · seed-2-0-lite-260428 · thinking:enabled · medium · temp:1',
  ],
  [
    c({
      provider: 'openai',
      model: 'gpt-5.4-mini',
      thinking: 'enabled',
      reasoning_effort: 'xhigh',
      temperature: 1.0,
    }),
    'OpenAI · gpt-5.4-mini · xhigh',
  ],
  [
    c({ provider: 'gemini', model: 'gemini-2.5-flash', reasoning_effort: 'medium' }),
    'Gemini · gemini-2.5-flash · medium',
  ],
];

describe('deriveConfigName · 黃金清單（與後端逐字對齊）', () => {
  it.each(GOLDEN)('%o → %s', (cfg, expected) => {
    expect(deriveConfigName(cfg)).toBe(expected);
  });

  it('全部互異（名稱即身分，撞名代表去重失效）', () => {
    const names = GOLDEN.map(([cfg]) => deriveConfigName(cfg));
    expect(new Set(names).size).toBe(names.length);
  });
});

describe('specKey · 折疊規則', () => {
  it('R1：effortOnly 供應商的 thinking 一律折成 default', () => {
    expect(specKey(c({ provider: 'openai', model: 'gpt-5.4-mini', thinking: 'enabled' }))[2]).toBe(
      'default',
    );
    expect(
      specKey(c({ provider: 'gemini', model: 'gemini-3.5-flash', thinking: 'disabled' }))[2],
    ).toBe('default');
  });

  it('R2：nativeSwitch 在 thinking=disabled/auto 下 effort 折成 default（執行層不送）', () => {
    const base = c({
      provider: 'bytedance',
      model: 'seed-2-0-lite-260228',
      reasoning_effort: 'high',
    });
    expect(specKey(c({ ...base, thinking: 'disabled' }))[3]).toBe('default');
    expect(specKey(c({ ...base, thinking: 'auto' }))[3]).toBe('default');
    expect(specKey(c({ ...base, thinking: 'enabled' }))[3]).toBe('high'); // enabled 才保留
  });

  it('R2 的後果：thinking=disabled 下 effort 不同的兩筆是**同一個**配置', () => {
    const a = c({
      provider: 'bytedance',
      model: 'seed-2-0-lite-260228',
      thinking: 'disabled',
      reasoning_effort: 'high',
    });
    const b = c({
      provider: 'bytedance',
      model: 'seed-2-0-lite-260228',
      thinking: 'disabled',
      reasoning_effort: 'low',
    });
    expect(specKeyOf(a)).toBe(specKeyOf(b));
  });

  it('R3 的 round(2)：UI step 是 0.1，兩位小數無損（此處 effort=default 未推理，值不會被折）', () => {
    const at = (t: number | null) =>
      specKey(c({ provider: 'openai', model: 'gpt-5.4', temperature: t }))[4];
    expect(at(1)).toBe(1);
    expect(at(0.1000001)).toBe(0.1);
    expect(at(null)).toBeNull();
  });

  it('R3 折疊：**送了也沒用**才折，會被真的採用就保留', () => {
    // 依 2026-07-31 逐 model 實測（144 次真實 API 呼叫）：
    const t = (o: Partial<LlmModelConfig>) => specKey(c(o))[4];
    // gpt-5.4-mini + 推理生效 → API 只接受預設溫度 → 折
    expect(
      t({ provider: 'openai', model: 'gpt-5.4-mini', reasoning_effort: 'medium', temperature: 1 }),
    ).toBeNull();
    // 同 model 但未推理 → 可自訂 → 不折
    expect(t({ provider: 'openai', model: 'gpt-5.4-mini', temperature: 0.3 })).toBe(0.3);
    // gpt-5.5 任何狀態都只接受預設 → 折
    expect(t({ provider: 'openai', model: 'gpt-5.5', temperature: 1 })).toBeNull();
    // ByteDance 實測受理 0.3 → 不折（折了就是改變送出內容）
    expect(
      t({
        provider: 'bytedance',
        model: 'seed-2-0-lite-260228',
        thinking: 'enabled',
        temperature: 0.3,
      }),
    ).toBe(0.3);
  });

  it('折疊讓「實際送出內容相同」的兩筆規格鍵相同（否則會並存且名字暗示假差異）', () => {
    const plain = c({ provider: 'openai', model: 'gpt-5.4-mini', reasoning_effort: 'medium' });
    expect(specKeyOf({ ...plain, temperature: 1 })).toBe(specKeyOf(plain));
  });

  it('temperature=0 不被當成「未設定」（falsy 陷阱）', () => {
    expect(deriveConfigName(c({ provider: 'openai', model: 'gpt-5.4', temperature: 0 }))).toBe(
      'OpenAI · gpt-5.4 · temp:0',
    );
  });

  it('自訂 model 名下仍以 provider 判別能力，不誤折 ByteDance 的 thinking', () => {
    // 判別軸若退回「由 model 反推」，未登記的 model 會靜默歸 openai → thinking 被錯誤折掉，
    // 但執行層（看 provider）其實會送它 → 名稱與實跑不符。
    expect(
      specKey(c({ provider: 'bytedance', model: 'my-custom-gw', thinking: 'enabled' }))[2],
    ).toBe('enabled');
  });

  it('provider/model 皆空 → 回空字串（新增草稿未填完的中間態，不該顯示「未知供應商」）', () => {
    expect(deriveConfigName({})).toBe('');
  });
});

import { describe, expect, it } from 'vitest';
import {
  canEnterStep,
  clampStep,
  highestReachableStep,
  isRegressionStale,
  publishBlockedReason,
  sameIdSet,
  stepBlockedReason,
  type PipelineState,
} from './pipelineGate.util';

/** 建一個狀態切片；預設是「什麼都還沒做」，各測試只覆寫關心的欄位。 */
const state = (over: Partial<PipelineState> = {}): PipelineState => ({
  selectedIds: [],
  candidatePrompt: '',
  regressionDone: false,
  ...over,
});

const READY = state({ selectedIds: [1], candidatePrompt: 'X', regressionDone: true });

describe('步驟閘門', () => {
  it('① 恆可進入', () => {
    expect(canEnterStep(1, state())).toBe(true);
    expect(stepBlockedReason(1, state())).toBe('');
  });

  it('未勾案例 → ② 不可進入', () => {
    expect(canEnterStep(2, state())).toBe(false);
    expect(stepBlockedReason(2, state())).toContain('①');
  });

  it('無候選 Prompt → ③ 不可進入（即使已勾案例）', () => {
    const s = state({ selectedIds: [1, 2] });
    expect(canEnterStep(2, s)).toBe(true);
    expect(canEnterStep(3, s)).toBe(false);
    expect(stepBlockedReason(3, s)).toContain('②');
  });

  it('回歸未跑完 → ④ 不可進入', () => {
    const s = state({ selectedIds: [1], candidatePrompt: 'X' });
    expect(canEnterStep(3, s)).toBe(true);
    expect(canEnterStep(4, s)).toBe(false);
    expect(stepBlockedReason(4, s)).toContain('③');
  });

  it('全部條件到位 → ④ 可進入', () => {
    expect(canEnterStep(4, READY)).toBe(true);
    expect(stepBlockedReason(4, READY)).toBe('');
  });

  it('擋住原因回報**最早**沒滿足的那一關，不是最後一關', () => {
    // 什麼都沒做卻想跳 ④：該說「先去①」，而不是「先去③」——否則使用者照做也還是進不去
    expect(stepBlockedReason(4, state())).toContain('①');
  });
});

describe('highestReachableStep / clampStep（query 還原）', () => {
  it('逐級遞進', () => {
    expect(highestReachableStep(state())).toBe(1);
    expect(highestReachableStep(state({ selectedIds: [1] }))).toBe(2);
    expect(highestReachableStep(state({ selectedIds: [1], candidatePrompt: 'X' }))).toBe(3);
    expect(highestReachableStep(READY)).toBe(4);
  });

  it('query 帶的步驟超過可達 → 夾到可達的最高步（重整後只會回到①）', () => {
    expect(clampStep(3, state())).toBe(1);
    expect(clampStep(4, state({ selectedIds: [1] }))).toBe(2);
    expect(clampStep(4, READY)).toBe(4);
  });

  it('可達範圍內照給，不強制推到最高步', () => {
    expect(clampStep(1, READY)).toBe(1);
    expect(clampStep(2, READY)).toBe(2);
  });

  it('髒輸入一律夾回合法範圍', () => {
    expect(clampStep(0, READY)).toBe(1);
    expect(clampStep(-3, READY)).toBe(1);
    expect(clampStep(99, READY)).toBe(4);
    expect(clampStep(Number.NaN, READY)).toBe(1);
    expect(clampStep(2.7, READY)).toBe(2);
  });
});

describe('sameIdSet', () => {
  it('順序不同視為同一集合——回①只是回頭看一眼，不該清掉回歸結果', () => {
    expect(sameIdSet([1, 2, 3], [3, 1, 2])).toBe(true);
  });

  it('重複值不影響集合相等', () => {
    expect(sameIdSet([1, 1, 2], [2, 1])).toBe(true);
  });

  it('真的增刪就不是同一集合', () => {
    expect(sameIdSet([1, 2], [1, 2, 3])).toBe(false);
    expect(sameIdSet([1, 2], [1])).toBe(false);
    expect(sameIdSet([1, 2], [1, 3])).toBe(false);
  });

  it('兩邊皆空＝相等', () => {
    expect(sameIdSet([], [])).toBe(true);
  });
});

describe('isRegressionStale（防「拿 A 的綠燈發布 B」）', () => {
  const at = { validatedPrompt: 'A', validatedIds: [1, 2] };

  it('還沒跑過回歸 → 無所謂失效', () => {
    expect(isRegressionStale(null, { candidatePrompt: 'A', selectedIds: [1, 2] })).toBe(false);
  });

  it('候選 Prompt 沒變、案例沒變 → 結果仍有效', () => {
    expect(isRegressionStale(at, { candidatePrompt: 'A', selectedIds: [2, 1] })).toBe(false);
  });

  it('候選 Prompt 變了 → 失效（回②取消補丁重套的路徑）', () => {
    expect(isRegressionStale(at, { candidatePrompt: 'B', selectedIds: [1, 2] })).toBe(true);
  });

  it('案例集合變了 → 失效（回①增刪案例的路徑；草案原本漏掉這個入口）', () => {
    expect(isRegressionStale(at, { candidatePrompt: 'A', selectedIds: [1, 2, 3] })).toBe(true);
    expect(isRegressionStale(at, { candidatePrompt: 'A', selectedIds: [1] })).toBe(true);
  });
});

describe('publishBlockedReason（存草稿與升版的門檻不同）', () => {
  it('尚未存草稿 → 不能升版（升版來源必須是已存檔的草稿）', () => {
    expect(publishBlockedReason(0, false)).toContain('存為新草稿');
  });

  it('有改壞 → 擋住升版，並講清楚幾個欄', () => {
    expect(publishBlockedReason(3, true)).toContain('3');
  });

  it('零改壞且已存草稿 → 放行', () => {
    expect(publishBlockedReason(0, true)).toBe('');
  });

  it('改壞不影響「存為新草稿」——本函式只管升版，草稿是另一條路徑', () => {
    // 存草稿的唯一前提是有候選版；publishBlockedReason 從不參與那個判斷
    expect(publishBlockedReason(5, false)).not.toContain('改壞');
  });
});

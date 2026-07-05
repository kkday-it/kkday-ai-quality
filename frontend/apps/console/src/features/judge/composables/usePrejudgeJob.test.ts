import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { computed, ref } from 'vue';

vi.mock('@arco-design/web-vue', () => ({ Message: { success: vi.fn(), warning: vi.fn(), info: vi.fn(), error: vi.fn() } }));
vi.mock('@/api', () => ({
  getProblems: vi.fn(),
  startPrejudge: vi.fn(),
  pausePrejudge: vi.fn(),
  resumePrejudge: vi.fn(),
  cancelPrejudge: vi.fn(),
  prejudgeStreamUrl: (id: string) => `/stream?job_id=${id}`,
}));

import { getProblems, startPrejudge } from '@/api';
import { usePrejudgeJob } from './usePrejudgeJob';

const getProblemsMock = vi.mocked(getProblems);
const startPrejudgeMock = vi.mocked(startPrejudge);

/** SSE stub：建立即以微任務推 done（讓 _poll 綁好 onmessage 後才觸發）。 */
class MockEventSource {
  onmessage: ((e: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(_url: string) {
    queueMicrotask(() =>
      this.onmessage?.({ data: JSON.stringify({ status: 'done', processed: 2, total: 2 }) }),
    );
  }
  close() {}
}

const mk = (opts: { selected?: string[]; verticals?: string[] } = {}) =>
  usePrejudgeJob({
    source: () => 'product_reviews',
    llmConfigId: ref('cfg1'),
    effVerticals: computed(() => opts.verticals),
    selectedKeys: ref(opts.selected ?? []),
    reload: vi.fn().mockResolvedValue(undefined),
  });

beforeEach(() => {
  vi.stubGlobal('EventSource', MockEventSource);
  getProblemsMock.mockReset();
  // 安全預設：openPrejudge 會 fire-and-forget refreshTargetCount，未設 mock 時避免 undefined.total 拋 unhandled rejection。
  getProblemsMock.mockResolvedValue({ rows: [], total: 0 });
  startPrejudgeMock.mockReset();
  startPrejudgeMock.mockResolvedValue({ job_id: 'j1', total: 2, model: 'gpt-5-nano' });
});
afterEach(() => vi.unstubAllGlobals());

describe('usePrejudgeJob 目標選取', () => {
  it('openPrejudge：有勾選→selected 模式；無勾選→scope', () => {
    const withSel = mk({ selected: ['a'] });
    withSel.openPrejudge();
    expect(withSel.targetMode.value).toBe('selected');
    expect(withSel.confirmOpen.value).toBe(true);

    const noSel = mk();
    noSel.openPrejudge();
    expect(noSel.targetMode.value).toBe('scope');
  });

  it('hasJudgedStage：含非 unjudged 階段才 true', () => {
    const job = mk();
    expect(job.hasJudgedStage.value).toBe(false); // 預設 ['unjudged']
    job.targetStages.value = ['unjudged', 'pending_review'];
    expect(job.hasJudgedStage.value).toBe(true);
  });

  it('refreshTargetCount：selected 模式＝勾選數（不打 API）', async () => {
    const job = mk({ selected: ['a', 'b', 'c'] });
    job.targetMode.value = 'selected';
    await job.refreshTargetCount();
    expect(job.targetCount.value).toBe(3);
    expect(getProblemsMock).not.toHaveBeenCalled();
  });

  it('refreshTargetCount：scope 模式逐階段查 total 加總', async () => {
    getProblemsMock.mockResolvedValue({ rows: [], total: 7 });
    const job = mk();
    job.targetMode.value = 'scope';
    job.targetStages.value = ['unjudged', 'pending_review'];
    await job.refreshTargetCount();
    expect(job.targetCount.value).toBe(14); // 7 + 7
    // unjudged → judged:false；其餘 → stage=[st]
    expect(getProblemsMock).toHaveBeenCalledWith(expect.objectContaining({ judged: false }));
    expect(getProblemsMock).toHaveBeenCalledWith(expect.objectContaining({ stage: ['pending_review'] }));
  });
});

describe('usePrejudgeJob doRun body 建構', () => {
  it('selected 模式 → item_ids + source', async () => {
    const job = mk({ selected: ['x', 'y'] });
    job.targetMode.value = 'selected';
    job.doRun();
    await vi.waitFor(() => expect(startPrejudgeMock).toHaveBeenCalledTimes(1));
    expect(startPrejudgeMock).toHaveBeenCalledWith(
      expect.objectContaining({ item_ids: ['x', 'y'], source: 'product_reviews', llm_config_id: 'cfg1' }),
    );
    expect(job.confirmOpen.value).toBe(false);
  });

  it('scope 模式 + 僅 unjudged → 不帶傾向/信心收斂', async () => {
    const job = mk({ verticals: ['Tour'] });
    job.targetMode.value = 'scope';
    job.targetStages.value = ['unjudged'];
    job.doRun();
    await vi.waitFor(() => expect(startPrejudgeMock).toHaveBeenCalledTimes(1));
    const body = startPrejudgeMock.mock.calls[0][0];
    expect(body).toMatchObject({ source: 'product_reviews', scope: 'all', product_verticals: ['Tour'], stages: ['unjudged'] });
    expect(body).not.toHaveProperty('target_polarity');
    expect(body).not.toHaveProperty('max_confidence');
  });

  it('scope 模式 + 含已判階段 + lowConfOnly → 帶 target_polarity + max_confidence(auto_accept)', async () => {
    const job = mk();
    job.targetMode.value = 'scope';
    job.targetStages.value = ['unjudged', 'pending_review'];
    job.targetPolarity.value = 'negative';
    job.lowConfOnly.value = true;
    job.doRun();
    await vi.waitFor(() => expect(startPrejudgeMock).toHaveBeenCalledTimes(1));
    const body = startPrejudgeMock.mock.calls[0][0];
    expect(body.target_polarity).toBe('negative');
    expect(typeof body.max_confidence).toBe('number'); // ＝judgment.confidence_tiers.auto_accept
  });
});

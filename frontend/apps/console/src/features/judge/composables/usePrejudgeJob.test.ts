import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { computed, ref } from 'vue';

vi.mock('@arco-design/web-vue', () => ({
  Message: { success: vi.fn(), warning: vi.fn(), info: vi.fn(), error: vi.fn() },
}));
vi.mock('@/api', () => ({
  getProblems: vi.fn(),
  previewPrejudgeCount: vi.fn(),
  startPrejudge: vi.fn(),
  pausePrejudge: vi.fn(),
  resumePrejudge: vi.fn(),
  cancelPrejudge: vi.fn(),
  prejudgeStreamUrl: (id: string) => `/stream?job_id=${id}`,
  prejudgeLogStreamUrl: (id: string) => `/log-stream?job_id=${id}`,
}));

import { getProblems, previewPrejudgeCount, startPrejudge } from '@/api';
import { usePrejudgeJob } from './usePrejudgeJob';

const getProblemsMock = vi.mocked(getProblems);
const previewCountMock = vi.mocked(previewPrejudgeCount);
const startPrejudgeMock = vi.mocked(startPrejudge);

/** SSE stub：建立即以微任務逐條播放 `MockEventSource.script`（讓 _poll/_openLog 綁好
 *  handler 後才觸發）。條目：`{ event: 'error' }`＝觸發無 data 的 error event（模擬連線瞬斷，
 *  吃 errStreak 重連邏輯）；其餘＝進度 snapshot 走 onmessage。預設腳本＝立即 done。 */
class MockEventSource {
  static script: Array<Record<string, unknown>> = [{ status: 'done', processed: 2, total: 2 }];
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  private listeners: Record<string, Array<(e: { data?: string }) => void>> = {};
  constructor(_url: string) {
    queueMicrotask(() => {
      this.onopen?.();
      for (const item of MockEventSource.script) {
        if (item.event === 'error') this.listeners['error']?.forEach((f) => f({}));
        else this.onmessage?.({ data: JSON.stringify(item) });
      }
    });
  }
  addEventListener(type: string, fn: (e: { data?: string }) => void) {
    (this.listeners[type] ??= []).push(fn);
  }
  close() {}
}

const mk = (
  opts: { selected?: string[]; verticals?: string[]; filters?: Record<string, unknown> } = {},
) =>
  usePrejudgeJob({
    source: () => 'product_reviews',
    llmConfigId: ref('cfg1'),
    effVerticals: computed(() => opts.verticals),
    selectedKeys: ref(opts.selected ?? []),
    listFilters: computed(() => opts.filters ?? {}),
    reload: vi.fn().mockResolvedValue(undefined),
  });

beforeEach(() => {
  vi.stubGlobal('EventSource', MockEventSource);
  MockEventSource.script = [{ status: 'done', processed: 2, total: 2 }];
  getProblemsMock.mockReset();
  previewCountMock.mockReset();
  // 安全預設：openPrejudge 會 fire-and-forget refreshTargetCount，未設 mock 時避免 undefined.total 拋 unhandled rejection。
  getProblemsMock.mockResolvedValue({ rows: [], total: 0 });
  previewCountMock.mockResolvedValue({ total: 0 });
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

  it('refreshTargetCount：selected 模式＝以 within_ids 打 count 端點（預覽=實跑同 body）', async () => {
    previewCountMock.mockResolvedValue({ total: 3 });
    const job = mk({ selected: ['a', 'b', 'c'] });
    job.targetMode.value = 'selected';
    await job.refreshTargetCount();
    expect(job.targetCount.value).toBe(3);
    // selected 模式：scope='all' + within_ids 交集勾選列（與 doRun 同一 body）
    expect(previewCountMock).toHaveBeenCalledWith(
      expect.objectContaining({ scope: 'all', within_ids: ['a', 'b', 'c'] }),
    );
  });

  it('refreshTargetCount：scope 模式以 stages 打 count 端點（單次，total 即結果）', async () => {
    previewCountMock.mockResolvedValue({ total: 14 });
    const job = mk();
    job.targetMode.value = 'scope';
    job.targetStages.value = ['unjudged', 'pending_review'];
    await job.refreshTargetCount();
    expect(job.targetCount.value).toBe(14);
    // scope 模式：within_ids 不帶，stages 驅動
    expect(previewCountMock).toHaveBeenCalledWith(
      expect.objectContaining({ scope: 'all', stages: ['unjudged', 'pending_review'] }),
    );
  });
});

describe('usePrejudgeJob doRun body 建構', () => {
  it('selected 模式 → within_ids + scope=all（scope 目標選取，非 item_ids）', async () => {
    const job = mk({ selected: ['x', 'y'] });
    job.targetMode.value = 'selected';
    job.doRun();
    await vi.waitFor(() => expect(startPrejudgeMock).toHaveBeenCalledTimes(1));
    expect(startPrejudgeMock).toHaveBeenCalledWith(
      expect.objectContaining({
        within_ids: ['x', 'y'],
        scope: 'all',
        source: 'product_reviews',
        llm_config_id: 'cfg1',
      }),
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
    expect(body).toMatchObject({
      source: 'product_reviews',
      scope: 'all',
      product_verticals: ['Tour'],
      stages: ['unjudged'],
    });
    expect(body).not.toHaveProperty('target_polarity');
    expect(body).not.toHaveProperty('max_confidence');
  });

  it('scope 模式 + 含已初判階段 + lowConfOnly → 帶 target_polarity + max_confidence(auto_accept)', async () => {
    const job = mk();
    job.targetMode.value = 'scope';
    job.targetStages.value = ['unjudged', 'pending_review'];
    job.draftFilters.polarity = ['negative'];
    job.lowConfOnly.value = true;
    job.doRun();
    await vi.waitFor(() => expect(startPrejudgeMock).toHaveBeenCalledTimes(1));
    const body = startPrejudgeMock.mock.calls[0][0];
    expect(body.target_polarity).toEqual(['negative']);
    expect(typeof body.max_confidence).toBe('number'); // ＝judgment.confidence_tiers.auto_accept
  });

  it('scope 模式 + 目標篩選草稿（openPrejudge 以頁面篩選初始化）→ 表級全帶、初判級只在已初判階段帶', async () => {
    const filters = {
      polarity: ['negative'],
      confidenceTier: 'jury',
      taxonomy: ['content'],
      dateFrom: '2026-07-01',
      dateTo: '2026-07-07',
      prodOid: 'P1',
    };
    // 僅未初判：表級（日期/oid）帶、初判級（confidence_tier/taxonomy）不帶
    const job = mk({ filters });
    job.openPrejudge(); // 初始化草稿（來自頁面篩選）
    job.targetMode.value = 'scope';
    job.targetStages.value = ['unjudged'];
    job.doRun();
    await vi.waitFor(() => expect(startPrejudgeMock).toHaveBeenCalledTimes(1));
    const body = startPrejudgeMock.mock.calls[0][0];
    expect(body).toMatchObject({ date_from: '2026-07-01', date_to: '2026-07-07', prod_oid: 'P1' });
    expect(body).not.toHaveProperty('confidence_tier');

    // 含已初判階段：初判級收斂一併帶上
    const job2 = mk({ filters });
    job2.openPrejudge();
    job2.targetMode.value = 'scope';
    job2.targetStages.value = ['pending_review'];
    job2.doRun();
    await vi.waitFor(() => expect(startPrejudgeMock).toHaveBeenCalledTimes(2));
    const body2 = startPrejudgeMock.mock.calls[1][0];
    expect(body2).toMatchObject({ confidence_tier: 'jury', taxonomy: ['content'] });
  });

  it('scope 模式 + 清空草稿 → 不帶任何列表維度（草稿即最終口徑，非頁面篩選）', async () => {
    const job = mk({ filters: { taxonomy: ['content'], confidenceTier: 'jury' } });
    job.openPrejudge(); // 草稿初始化為頁面篩選
    job.targetMode.value = 'scope';
    job.draftFilters.taxonomy = []; // 使用者於彈窗清空
    job.draftFilters.tier = '';
    job.targetStages.value = ['unjudged'];
    job.doRun();
    await vi.waitFor(() => expect(startPrejudgeMock).toHaveBeenCalledTimes(1));
    const body = startPrejudgeMock.mock.calls[0][0];
    expect(body.taxonomy).toBeUndefined();
    expect(body).not.toHaveProperty('confidence_tier');
  });

  it('openPrejudge：頁面傾向篩選（多選）整組帶入草稿作為再判收斂傾向預設', () => {
    const job = mk({ filters: { polarity: ['neutral'] } });
    job.openPrejudge();
    expect(job.draftFilters.polarity).toEqual(['neutral']);
  });
});

describe('usePrejudgeJob 終態快照 lastRun', () => {
  it('done 終態：lastRun 填入結果快照，jobStatus/running 照舊清空', async () => {
    MockEventSource.script = [
      { status: 'running', processed: 1, total: 2, total_tokens: 100, cost_usd: 0.01 },
      { status: 'done', processed: 2, total: 2, total_tokens: 250, cost_usd: 0.025 },
    ];
    const job = mk();
    job.doRun();
    await vi.waitFor(() => expect(job.lastRun.value).not.toBeNull());
    expect(job.lastRun.value).toMatchObject({
      jobId: 'j1',
      status: 'done',
      processed: 2,
      total: 2,
      totalTokens: 250,
      costUsd: 0.025,
      model: 'gpt-5-nano',
    });
    expect(job.jobStatus.value).toBe(''); // finally 清空行為不變
    expect(job.running.value).toBe(false);
  });

  it('error 終態：lastRun.status=error', async () => {
    MockEventSource.script = [{ status: 'error', processed: 1, total: 2 }];
    const job = mk();
    job.doRun();
    await vi.waitFor(() => expect(job.lastRun.value).not.toBeNull());
    expect(job.lastRun.value?.status).toBe('error');
  });

  it('連線中斷放手（非終態）：lastRun 不寫入（避免假終態）', async () => {
    MockEventSource.script = Array.from({ length: 6 }, () => ({ event: 'error' }));
    const job = mk();
    job.doRun();
    await vi.waitFor(() => expect(job.running.value).toBe(false));
    expect(job.lastRun.value).toBeNull();
  });

  it('openPrejudge 起手清空 lastRun（開新一輪設定不殘留舊摘要）', async () => {
    const job = mk();
    job.doRun();
    await vi.waitFor(() => expect(job.lastRun.value).not.toBeNull());
    job.openPrejudge();
    expect(job.lastRun.value).toBeNull();
  });

  it('rejudgeRow（單列）：起手清空、done 終態填入快照', async () => {
    const job = mk();
    await job.rejudgeRow('r1');
    expect(job.lastRun.value).toMatchObject({ jobId: 'j1', status: 'done', model: 'gpt-5-nano' });
  });
});

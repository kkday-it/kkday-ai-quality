import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@arco-design/web-vue', () => ({
  Message: { success: vi.fn(), error: vi.fn() },
}));

/** 一則反饋：兩條有效歸因 + 一條已標記誤判（工作台要處理的完整局面）。 */
const RECORD = {
  live: [
    { attribution_oid: 11, l2: { code: 'C-1-1', label: '行程資訊' }, sentiment_score: 2 },
    { attribution_oid: 12, l2: { code: 'C-3-1', label: '餐飲品質' }, sentiment_score: 1 },
  ],
  deleted: [{ attribution_oid: 13, l2: { code: 'S-2-1', label: '約定履行' }, sentiment_score: 2 }],
  human_managed: true,
  suggestion_count: 2,
};

const api = vi.hoisted(() => ({
  getCorrectionPolicy: vi.fn(async () => ({
    editable_fields: ['l1_code', 'l2_code', 'polarity', 'sentiment_score'],
    reason_min_length: 5,
    reason_max_length: 500,
  })),
  getRecordAttributions: vi.fn(),
  // 參數型別要宣告，否則 `mock.lastCall` 會被推成空 tuple、斷言取不到欄位
  correctAttribution: vi.fn(
    async (_b: { changes: Record<string, unknown>; attribution_oid: number }) => ({
      attribution: {},
    }),
  ),
  createAttribution: vi.fn(async () => ({ attribution: {} })),
  deleteAttribution: vi.fn(async () => ({ attribution: {} })),
  restoreAttribution: vi.fn(async () => ({ attribution: {} })),
  confirmAttribution: vi.fn(async (_b: { confirmed_fields: string[] }) => ({ attribution: {} })),
  swapAttributionSlots: vi.fn(
    async (_b: { attribution_oid_a: number; attribution_oid_b: number }) => ({
      attributions: [],
      changed: {},
    }),
  ),
}));
vi.mock('@/api', () => api);

const { useAttributionCorrection } = await import('./useAttributionCorrection');

type Ctl = ReturnType<typeof useAttributionCorrection>;

const openWorkbench = async (): Promise<Ctl> => {
  const ctl = useAttributionCorrection();
  await ctl.openFor('reviews', 'R1');
  return ctl;
};

beforeEach(() => {
  vi.clearAllMocks();
  api.getRecordAttributions.mockResolvedValue(structuredClone(RECORD));
});

describe('工作台是反饋級的：一次拿到全部歸因', () => {
  it('有效與已標記誤判分成兩組，兩組都拿得到', async () => {
    const ctl = await openWorkbench();
    expect(api.getRecordAttributions).toHaveBeenCalledWith('reviews', 'R1');
    expect(ctl.live.value.map((a) => a.attribution_oid)).toEqual([11, 12]);
    expect(ctl.deleted.value.map((a) => a.attribution_oid)).toEqual([13]);
    expect(ctl.humanManaged.value).toBe(true);
    expect(ctl.suggestionCount.value).toBe(2);
  });

  it('佔用面向**含 tombstone**——後端對重複面向一律 409，tombstone 也算佔用', async () => {
    const ctl = await openWorkbench();
    expect([...ctl.occupiedSlots.value.keys()].sort()).toEqual(['C-1-1', 'C-3-1', 'S-2-1']);
    expect(ctl.occupiedSlots.value.get('S-2-1')).toEqual({ oid: 13, dismissed: true });
    expect(ctl.occupiedSlots.value.get('C-1-1')).toEqual({ oid: 11, dismissed: false });
  });
});

describe('送出守門', () => {
  let ctl: Ctl;
  beforeEach(async () => {
    ctl = await openWorkbench();
    ctl.startEdit(RECORD.live[0]);
  });

  it('理由沒填或太短就不能送出（設計核心，不是形式主義）', () => {
    ctl.editDraft.l2_code = 'C-3-2';
    ctl.editDraft.reason = '錯';
    expect(ctl.canSubmitEdit.value).toBe(false);

    ctl.editDraft.reason = 'AI 把出發時間誤解為集合時間';
    expect(ctl.canSubmitEdit.value).toBe(true);
  });

  it('沒動任何欄位＝空提交，擋下', () => {
    ctl.editDraft.reason = 'AI 把出發時間誤解為集合時間';
    expect(ctl.canSubmitEdit.value).toBe(false);
  });

  it('新增必須有分類、情緒分與摘要（傾向由情緒分派生，不是輸入項）', () => {
    ctl.startCreate();
    ctl.createDraft.reason = 'AI 完全沒判到這個面向';
    expect(ctl.canSubmitCreate.value).toBe(false);

    ctl.createDraft.l2_code = 'C-3-1';
    ctl.createDraft.sentiment_score = 1;
    expect(ctl.canSubmitCreate.value, '缺摘要仍不可送出').toBe(false);

    ctl.createDraft.summary = '現場退款糾紛';
    expect(ctl.canSubmitCreate.value).toBe(true);
  });

  it('畫面過期後一律擋住送出——oid 已全部換新，送出去只會逐條 404', async () => {
    ctl.editDraft.sentiment_score = 3;
    ctl.editDraft.reason = 'AI 把中立敘述當成抱怨';
    expect(ctl.canSubmitEdit.value).toBe(true);

    ctl.stale.value = true;
    expect(ctl.canSubmitEdit.value).toBe(false);
  });
});

describe('提交行為', () => {
  it('只送出實際動過的欄；**不送 polarity**（它是後端由情緒分派生的值）', async () => {
    const ctl = await openWorkbench();
    ctl.startEdit(RECORD.live[0]);
    ctl.editDraft.sentiment_score = 3;
    ctl.editDraft.reason = 'AI 把中立敘述當成抱怨';

    await ctl.submitEdit(11);

    expect(api.correctAttribution).toHaveBeenCalledTimes(1);
    const [body] = api.correctAttribution.mock.lastCall ?? [];
    expect(body?.changes).toEqual({ sentiment_score: 3 });
    expect(body?.changes).not.toHaveProperty('polarity');
    expect(body?.attribution_oid).toBe(11);
  });

  it('送出後**不關抽屜**、就地重載——留在工作台繼續處理下一條是核心體驗', async () => {
    const ctl = await openWorkbench();
    ctl.startEdit(RECORD.live[0]);
    ctl.editDraft.l2_code = 'C-3-2';
    ctl.editDraft.reason = 'AI 把出發時間誤解為集合時間';

    await ctl.submitEdit(11);

    expect(ctl.open.value, '抽屜被關掉了').toBe(true);
    expect(api.getRecordAttributions).toHaveBeenCalledTimes(2); // 開窗 1 次 + 送出後重載 1 次
    expect(ctl.justChangedOid.value).toBe(11);
    expect(ctl.sessionLog.value).toEqual([{ op: 'update', label: '修改' }]);
    expect(ctl.lastReason.value, '理由要留著供「沿用上一條理由」').toBe(
      'AI 把出發時間誤解為集合時間',
    );
  });

  it('高亮會自動淡出——不淡出的話連續改三條會有三條同時亮著，反而看不出剛動的是哪一條', async () => {
    vi.useFakeTimers();
    try {
      const ctl = await openWorkbench();
      ctl.startEdit(RECORD.live[0]);
      ctl.editDraft.l2_code = 'C-3-2';
      ctl.editDraft.reason = 'AI 把出發時間誤解為集合時間';
      await ctl.submitEdit(11);
      expect(ctl.justChangedOid.value, '送出後應立刻亮起').toBe(11);

      await vi.advanceTimersByTimeAsync(1500);
      expect(ctl.justChangedOid.value, '時間未到不該提早熄滅').toBe(11);

      await vi.advanceTimersByTimeAsync(1000); // 累計 2500ms > HIGHLIGHT_MS
      expect(ctl.justChangedOid.value, '到時必須自動淡出').toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it('標記誤判走 delete 端點，不需要任何欄位變更', async () => {
    const ctl = await openWorkbench();
    await ctl.dismiss(12, '這其實是正向回饋，AI 判反了');
    expect(api.deleteAttribution).toHaveBeenCalledTimes(1);
    expect(api.correctAttribution).not.toHaveBeenCalled();
    expect(ctl.sessionLog.value).toEqual([{ op: 'delete', label: '標記誤判' }]);
  });

  it('還原走 restore 端點', async () => {
    const ctl = await openWorkbench();
    await ctl.restore(13, '重看一次，AI 判得沒錯');
    expect(api.restoreAttribution).toHaveBeenCalledTimes(1);
  });

  it('互換走專用端點，不是拆成兩次 correct', async () => {
    const ctl = await openWorkbench();
    await ctl.swapWith(11, 12, 'AI 把兩個面向的內容寫反了');

    expect(api.swapAttributionSlots).toHaveBeenCalledTimes(1);
    expect(api.correctAttribution, '拆成兩次 correct 會在中途撞 409').not.toHaveBeenCalled();
    const [body] = api.swapAttributionSlots.mock.lastCall ?? [];
    expect(body?.attribution_oid_a).toBe(11);
    expect(body?.attribution_oid_b).toBe(12);
  });

  it('確認正確不需要理由（沒改值就沒有「為什麼改」可寫）', async () => {
    const ctl = await openWorkbench();
    await ctl.confirmCorrect(11, ['l1_code', 'l2_code']);
    const [body] = api.confirmAttribution.mock.lastCall ?? [];
    expect(body?.confirmed_fields).toEqual(['l1_code', 'l2_code']);
  });
});

describe('失敗處理', () => {
  it('404 翻成「畫面已過期」並設 stale——原始訊息是 oid 天書，使用者看不懂', async () => {
    const ctl = await openWorkbench();
    const err = Object.assign(new Error('歸因不存在或不屬於此反饋：oid=11'), { status: 404 });
    api.correctAttribution.mockRejectedValueOnce(err);

    ctl.startEdit(RECORD.live[0]);
    ctl.editDraft.sentiment_score = 3;
    ctl.editDraft.reason = 'AI 把中立敘述當成抱怨';
    await ctl.submitEdit(11);

    expect(ctl.stale.value).toBe(true);
    expect(ctl.sessionLog.value, '失敗不該記進本階段變更').toEqual([]);
  });

  it('其餘 4xx 直接顯示後端 detail（那是寫給人看的引導語）', async () => {
    const { Message } = await import('@arco-design/web-vue');
    const ctl = await openWorkbench();
    const err = Object.assign(new Error('此反饋已有該面向的歸因（oid=12），請直接修改那一條'), {
      status: 409,
    });
    api.correctAttribution.mockRejectedValueOnce(err);

    ctl.startEdit(RECORD.live[0]);
    ctl.editDraft.l2_code = 'C-3-1';
    ctl.editDraft.reason = '這條其實講的是餐飲';
    await ctl.submitEdit(11);

    expect(ctl.stale.value, '409 不是過期，不該設 stale').toBe(false);
    expect(Message.error).toHaveBeenCalledWith(
      '此反饋已有該面向的歸因（oid=12），請直接修改那一條',
    );
  });

  it('政策載入失敗時 fallback **不可為空陣列**——空白名單會讓後端擋掉任何修改', async () => {
    api.getCorrectionPolicy.mockRejectedValueOnce(new Error('boom'));
    const ctl = await openWorkbench();
    expect(ctl.policy.value?.editable_fields).toContain('l2_code');
    expect(ctl.policy.value?.editable_fields).toContain('sentiment_score');
  });
});

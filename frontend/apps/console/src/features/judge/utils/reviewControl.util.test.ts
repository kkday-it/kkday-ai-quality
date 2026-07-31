import { describe, expect, it } from 'vitest';
import { controlForField, defaultCorrection, optionsUnderParent } from './reviewControl.util';

/** 貼近後端 output_schema 真實形狀的最小樣本（欄名/約束取自 prompt_debug.output_schema）。 */
const schema = {
  type: 'object',
  properties: {
    L2: { type: 'string', enum: ['C01 憑證未送達', '其他'] },
    sentiment: { type: 'string', enum: ['positive', 'neutral', 'negative'] },
    urgency: { type: 'integer', minimum: 1, maximum: 5 },
    confidence: { type: 'number', minimum: 0, maximum: 1 },
    money_mention_flag: { type: 'boolean' },
    keywords: { type: 'array', maxItems: 5, items: { minLength: 2, maxLength: 6 } },
    summary: { type: 'string', minLength: 15, maxLength: 50 },
    big_int: { type: 'integer', minimum: 1, maximum: 999 },
  },
};

describe('controlForField', () => {
  it('enum 欄給下拉，選項逐字照 schema（不手抄）', () => {
    expect(controlForField(schema, 'L2')).toEqual({
      kind: 'select',
      options: ['C01 憑證未送達', '其他'],
    });
    expect(controlForField(schema, 'sentiment')).toEqual({
      kind: 'select',
      options: ['positive', 'neutral', 'negative'],
    });
  });

  it('小值域整數給分段按鈕，檔位鋪滿 min~max', () => {
    expect(controlForField(schema, 'urgency')).toEqual({ kind: 'radio', options: [1, 2, 3, 4, 5] });
  });

  it('值域過大的整數退回數字輸入（不排成一長列按鈕）', () => {
    expect(controlForField(schema, 'big_int')).toEqual({ kind: 'number', min: 1, max: 999 });
  });

  it('布林給開關、浮點給數字輸入、陣列給標籤輸入並帶單項長度約束', () => {
    expect(controlForField(schema, 'money_mention_flag')).toEqual({ kind: 'switch' });
    expect(controlForField(schema, 'confidence')).toEqual({ kind: 'number', min: 0, max: 1 });
    expect(controlForField(schema, 'keywords')).toEqual({
      kind: 'tags',
      maxItems: 5,
      itemMin: 2,
      itemMax: 6,
    });
  });

  it('字串欄給多行輸入並帶長度約束', () => {
    expect(controlForField(schema, 'summary')).toEqual({
      kind: 'textarea',
      minLength: 15,
      maxLength: 50,
    });
  });

  it('schema 認不出的欄退回多行輸入（不讓評判卡住）', () => {
    expect(controlForField(schema, '不存在的欄')).toEqual({
      kind: 'textarea',
      minLength: undefined,
      maxLength: undefined,
    });
    expect(controlForField(undefined, 'L2')).toEqual({
      kind: 'textarea',
      minLength: undefined,
      maxLength: undefined,
    });
  });
});

describe('defaultCorrection', () => {
  it('布林標錯＝直接翻面（人不同意 AI 的判定，反面就是答案）', () => {
    expect(defaultCorrection({ kind: 'switch' }, true)).toBe(false);
    expect(defaultCorrection({ kind: 'switch' }, false)).toBe(true);
  });

  it('AI 原值型別對得上就沿用，讓人改一處而非整欄重打', () => {
    expect(defaultCorrection({ kind: 'radio', options: [1, 2, 3, 4, 5] }, 3)).toBe(3);
    expect(defaultCorrection({ kind: 'number', min: 0, max: 1 }, 0.8)).toBe(0.8);
    expect(defaultCorrection({ kind: 'tags' }, ['憑證', '未到'])).toEqual(['憑證', '未到']);
    expect(defaultCorrection({ kind: 'select', options: ['a', 'b'] }, 'b')).toBe('b');
    expect(defaultCorrection({ kind: 'textarea' }, '主訴摘要')).toBe('主訴摘要');
  });

  it('AI 原值型別對不上就落回該控件的安全初值', () => {
    // 下拉：AI 值不在選項內（例如 schema 已改版）→ 留空，逼使用者明選
    expect(defaultCorrection({ kind: 'select', options: ['a', 'b'] }, 'z')).toBe('');
    expect(defaultCorrection({ kind: 'radio', options: [1, 2, 3] }, null)).toBe(1);
    expect(defaultCorrection({ kind: 'number', min: 2 }, 'x')).toBe(2);
    expect(defaultCorrection({ kind: 'tags' }, null)).toEqual([]);
    expect(defaultCorrection({ kind: 'textarea' }, null)).toBe('');
  });
});

describe('optionsUnderParent', () => {
  /** 貼近後端 output_cascade 的最小樣本：L1 → L2 → L3。 */
  const cascade = {
    L2: {
      parent: 'L1',
      options_by_parent: {
        '[101] 訂單取消': ['退款進度/狀態不透明', '取消政策本身僵化'],
        '[93] 訂單申請修改': ['特殊需求/加購無自助入口'],
        其他: ['其他'],
      },
    },
    L3: {
      parent: 'L2',
      options_by_parent: {
        退款進度或狀態不透明: ['退款作業時程長', 'unclear'],
        其他: ['n/a'],
      },
    },
  };

  it('依已選上層值收窄到該分支底下', () => {
    expect(optionsUnderParent(cascade, 'L2', '[101] 訂單取消')).toEqual([
      '退款進度/狀態不透明',
      '取消政策本身僵化',
    ]);
    expect(optionsUnderParent(cascade, 'L2', '[93] 訂單申請修改')).toEqual([
      '特殊需求/加購無自助入口',
    ]);
  });

  it('OOT 兩層都只剩單一合法值', () => {
    expect(optionsUnderParent(cascade, 'L2', '其他')).toEqual(['其他']);
    expect(optionsUnderParent(cascade, 'L3', '其他')).toEqual(['n/a']);
  });

  // 回 null（而非空陣列）讓呼叫端退回攤平值域——空陣列會把選單清空，人就卡住無法填正解
  it('無級聯規則／上層值不在表內／上層未選 → null 表示不限縮', () => {
    expect(optionsUnderParent(cascade, 'sentiment', 'positive')).toBeNull();
    expect(optionsUnderParent(cascade, 'L2', '[999]不存在的主題')).toBeNull();
    expect(optionsUnderParent(cascade, 'L2', '')).toBeNull();
    expect(optionsUnderParent(cascade, 'L2', undefined)).toBeNull();
    expect(optionsUnderParent(undefined, 'L2', '[101] 訂單取消')).toBeNull();
  });
});

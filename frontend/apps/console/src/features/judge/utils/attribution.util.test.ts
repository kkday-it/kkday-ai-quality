/**
 * `markOccupiedSlots` 的回歸鎖。
 *
 * 這支測試存在的理由是一次真實的靜默失敗：cascader 原本把**所有**已佔用面向都 disable，
 * 於是「與該條互換」的觸發條件（選中別條佔著的面向）永遠不成立，整個互換功能在 UI 上不可達，
 * 而且沒有任何錯誤——後端端點好好的、型別全過、既有測試全綠，只有真的去點才會發現。
 *
 * 所以這裡逐條斷言「哪些該 disable、哪些**必須**保持可選」，而不只是測 label 文字。
 */
import { describe, expect, it } from 'vitest';
import type { CascadeNode } from '@/api';
import { markOccupiedSlots, type OccupiedSlot } from './attribution.util';

const TREE: CascadeNode[] = [
  {
    value: 'quality',
    label: '商品品質',
    children: [
      { value: 'C-2-1', label: '住宿品質' },
      { value: 'C-2-2', label: '餐飲品質' },
      { value: 'C-2-3', label: '車輛設備' },
    ],
  },
  {
    value: 'content',
    label: '商品內容',
    children: [{ value: 'C-1-1', label: '商品定位' }],
  },
];

/** 把結果攤平成 `code → {disabled, label}`，斷言時不必逐層鑽。 */
const flat = (nodes: ReturnType<typeof markOccupiedSlots>) => {
  const out: Record<string, { disabled: boolean; label: string }> = {};
  const walk = (ns: typeof nodes) => {
    for (const n of ns) {
      out[n.value] = { disabled: n.disabled, label: n.label };
      if (n.children) walk(n.children);
    }
  };
  walk(nodes);
  return out;
};

const slots = (entries: [string, OccupiedSlot][]) => new Map(entries);

describe('markOccupiedSlots', () => {
  it('沒有任何佔用時全部可選、label 不加後綴', () => {
    const r = flat(markOccupiedSlots(TREE, slots([]), null, true));
    expect(Object.values(r).every((n) => !n.disabled)).toBe(true);
    expect(r['C-2-2'].label).toBe('餐飲品質');
  });

  it('存活列佔用 + 可互換 → 必須保持「可選」（互換入口的唯一觸發條件）', () => {
    const r = flat(markOccupiedSlots(TREE, slots([['C-2-2', { oid: 7, dismissed: false }]]), 5, true));
    // 這一條是重點：disabled 為 true 就等於整個互換功能不可達。
    expect(r['C-2-2'].disabled).toBe(false);
    expect(r['C-2-2'].label).toContain('可互換');
  });

  it('存活列佔用 + 不可互換（新增歸因）→ disable，因為沒有另一半可以換', () => {
    const r = flat(markOccupiedSlots(TREE, slots([['C-2-2', { oid: 7, dismissed: false }]]), null, false));
    expect(r['C-2-2'].disabled).toBe(true);
    expect(r['C-2-2'].label).toContain('已有歸因');
    expect(r['C-2-2'].label).not.toContain('可互換');
  });

  it('tombstone 佔用 → 一律 disable 且指路「需先還原」，即使開了 swappable', () => {
    const r = flat(markOccupiedSlots(TREE, slots([['C-2-2', { oid: 7, dismissed: true }]]), null, true));
    expect(r['C-2-2'].disabled).toBe(true);
    expect(r['C-2-2'].label).toContain('需先還原');
  });

  it('正在編輯的那一條自己佔的面向不算被佔用（否則改情緒分就選不回原面向）', () => {
    const r = flat(markOccupiedSlots(TREE, slots([['C-2-2', { oid: 7, dismissed: false }]]), 7, true));
    expect(r['C-2-2'].disabled).toBe(false);
    expect(r['C-2-2'].label).toBe('餐飲品質');
  });

  it('只標 L2，L1 域永遠可選（佔用是 L2 級的，鎖住 L1 會讓整個域選不進去）', () => {
    const r = flat(
      markOccupiedSlots(
        TREE,
        slots([
          ['C-2-1', { oid: 1, dismissed: false }],
          ['C-2-2', { oid: 2, dismissed: true }],
        ]),
        null,
        true,
      ),
    );
    expect(r.quality.disabled).toBe(false);
    expect(r.content.disabled).toBe(false);
  });

  it('不改動傳入的樹（呼叫端每次 render 都會呼叫，就地改會累積後綴）', () => {
    markOccupiedSlots(TREE, slots([['C-2-2', { oid: 7, dismissed: true }]]), null, true);
    expect(TREE[0].children![1].label).toBe('餐飲品質');
  });
});

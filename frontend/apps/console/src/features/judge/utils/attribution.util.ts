/**
 * 歸因的顯示衍生值（跨元件共用，避免同一份呈現邏輯各寫一份而漂移）。
 */
import type { CascadeNode } from '@/api';
import { POLARITY_LABELS, TIER_LABELS, type Attribution } from '../constants';
import { formatActor } from './actor.util';

/** L1 › L2 麵包屑（缺層自動略過）。 */
export const attributionPath = (a: Attribution | null | undefined): string =>
  [a?.l1?.label, a?.l2?.label].filter(Boolean).join(' › ') || '未歸因';

/**
 * 信心的顯示文字。
 *
 * 人工列刻意**不顯示數值**：`conf_value` 描述的是 AI 對**舊分類**的信心，掛在人改過的新分類上
 * 是謊言，所以後端在糾正時把它設為 NULL、只保留 `conf_tier='human'`（設 NULL 會讓人工列從分層
 * 篩選與 by_tier 聚合整批消失，故用字面值）。原始 AI 信心完整保存在事件流的前值快照裡。
 */
export const attributionConfidence = (a: Attribution): string => {
  if (a.origin === 'human') {
    return `人工判定${a.corrected_by ? ` · ${formatActor(a.corrected_by)}` : ''}`;
  }
  return [
    typeof a.confidence?.value === 'number' ? a.confidence.value.toFixed(2) : '—',
    TIER_LABELS[a.confidence?.tier || ''] || a.confidence?.tier || '',
  ]
    .filter(Boolean)
    .join(' · ');
};

/**
 * 一條歸因的描述區塊（左小標籤 + 右內容）。
 *
 * 抽屜內的表格欄數 >4 就要收斂成描述區塊（見 `.claude/rules/frontend-vue.md`），本函式即那個
 * 收斂後的呈現。糾正工作台與待審建議抽屜共用同一份，兩處的讀法才會一致。
 */
export const attributionLines = (a: Attribution | null): { k: string; v: string }[] => {
  if (!a) return [];
  return [
    { k: '歸因', v: attributionPath(a) },
    {
      k: '傾向',
      v: `${POLARITY_LABELS[a.polarity || ''] || a.polarity || '—'}${
        a.sentiment_score ? ` · 情緒分 ${a.sentiment_score}` : ''
      }`,
    },
    { k: '信心', v: attributionConfidence(a) },
  ];
};

/**
 * 時間軸／備註的時間顯示（ISO → `YYYY-MM-DD HH:mm:ss`）。
 *
 * 時間軸抽屜與糾正工作台的備註串共用同一份格式——兩處講的是同一條時間軸上的同一批事件，
 * 顯示成兩種樣子會讓人以為是不同的東西。
 */
export const fmtTimelineTime = (iso: string | null | undefined): string =>
  iso ? iso.replace('T', ' ').slice(0, 19) : '';

/** 某個 L2 面向被誰佔著（`useAttributionCorrection` 的 `occupiedSlots` 值型別）。 */
export interface OccupiedSlot {
  oid: number;
  dismissed: boolean;
}

/** cascade 節點 + 選項狀態（`CascadeNode` 是純資料樹，disabled 是這裡才加的呈現狀態）。 */
export interface CascadeOption extends CascadeNode {
  disabled: boolean;
  children?: CascadeOption[];
}

/**
 * 標記分類 cascade 樹上「已被本反饋佔用」的面向：**有出路的留著可選，死路才 disable**。
 *
 * 後端 `_assert_slot_free` 對重複面向一律回 409（tombstone 也算佔用）。工作台既然已拿到含
 * tombstone 的完整清單，就能在選之前先講清楚，而不是讓人送出後吃 409。但兩種佔用的出路不同：
 *
 * - **tombstone 佔用** → `disabled`。唯一出路是先還原那一條，編輯區裡沒有任何動作能成立。
 * - **存活列佔用 + `swappable`** → **保持可選**，選中後由呼叫端跳出「與該條互換」。這正是
 *   「AI 把兩個面向寫反了」的場景，互換是合法且唯一乾淨的解法。
 * - **存活列佔用 + 不可互換**（新增歸因時沒有另一半可換）→ `disabled`。
 *
 * ⚠️ **這裡曾經一律 disable，導致互換入口在 UI 上完全不可達（死碼）**。當時的註解寫「使用者
 * 可以用搜尋直接輸入」——2026-08-07 於 Arco 2.58 實測為**假**：`allow-search` 的結果列表照樣
 * 帶 `arco-cascader-option-disabled`，搜尋一樣點不下去。**多 disable 一個條件就讓整個功能消失，
 * 而且不報錯**——這是本函式被抽出來單測的原因。
 *
 * @param nodes 完整 cascade 樹（不會被修改，回傳新樹）
 * @param occupied L2 code → 佔用者
 * @param excludeOid 正在編輯的那一條——它自己佔著的面向不算被佔用（不然改情緒分就選不回原面向）
 * @param swappable 選中存活列佔用的面向時是否有互換這條出路（改既有＝有；新增＝沒有）
 */
export const markOccupiedSlots = (
  nodes: CascadeNode[],
  occupied: Map<string, OccupiedSlot>,
  excludeOid: number | null,
  swappable = false,
): CascadeOption[] =>
  nodes.map((n) => {
    const hit = occupied.get(n.value);
    const taken = !!hit && hit.oid !== excludeOid;
    const canSwap = taken && !hit!.dismissed && swappable;
    return {
      ...n,
      disabled: taken && !canSwap,
      label: taken
        ? `${n.label}（${hit!.dismissed ? '已標記誤判，需先還原' : canSwap ? '已有歸因 · 可互換' : '已有歸因'}）`
        : n.label,
      children: n.children
        ? markOccupiedSlots(n.children, occupied, excludeOid, swappable)
        : undefined,
    };
  });

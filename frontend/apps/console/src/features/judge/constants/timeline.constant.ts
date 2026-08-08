/**
 * 反饋時間軸的「階段視圖」定義（列表操作區與歷史抽屜共讀這一份）。
 *
 * **一則反饋只有一條時間軸**——初判快照、初判失敗、人工糾正、複審確認、待審建議、備註全部按時間
 * 排在同一條軸上。這裡切的是「看哪一段」，不是開第二條軸。
 *
 * 為什麼不做成「初判歷史」「判決歷史」兩個各自獨立的抽屜：糾正、備註、待審建議這些事件同時跨初判
 * 與判決兩個階段，拆成兩條軸之後每加一種事件都要重新吵一次它該歸哪邊，而且使用者永遠看不到完整的
 * 先後順序（例如「AI 判了 → 人改了 → 重判產生建議」這條因果鏈會被切成兩半）。一條軸 + 階段過濾
 * 兩者都能給。
 */

/** 階段視圖代號。`verdict` 的事件型別尚未存在（判決功能未實作），先佔位以固定框架。 */
export type TimelineScope = 'all' | 'prejudge' | 'verdict' | 'human';

/**
 * 各視圖包含的事件 kind。`null` ＝不過濾（全部）。
 *
 * ⚠️ **新增事件 kind 時必須同時掛進這裡的某一段**，否則該事件只在 `all` 視圖看得到、
 * 在階段視圖裡靜默消失（與後端 `_USER_VISIBLE_KINDS` 是同一類的白名單維護稅）。
 */
export const TIMELINE_SCOPE_KINDS: Record<TimelineScope, string[] | null> = {
  all: null,
  prejudge: ['prejudge', 'failure'],
  // 判決事件尚未產生（判決歸因未實作）；框架先立好，補實作時只需往這個陣列加 kind。
  verdict: [],
  human: ['correction', 'review_confirm', 'suggestion', 'suggestion_resolved', 'note'],
};

/** 各視圖的抽屜標題。 */
export const TIMELINE_SCOPE_TITLE: Record<TimelineScope, string> = {
  all: '歷史時間軸',
  prejudge: '初判歷史',
  verdict: '判決歷史',
  human: '人工歷史',
};

/** 各視圖空清單時的說明（比通用的「暫無資料」更能讓人知道是哪一段沒有）。 */
export const TIMELINE_SCOPE_EMPTY: Record<TimelineScope, string> = {
  all: '這則反饋還沒有任何紀錄',
  prejudge: '這則反饋還沒有初判紀錄',
  verdict: '這則反饋還沒有判決紀錄',
  human: '這則反饋還沒有人工糾正或備註',
};

// 進線對話解析（純函式）：conversations content（conversation_full，新 30 欄格式）為單一展平字串，
// 段落（機器人／真人客服階段）以 ` ‖ ` 分隔、段落起首 `[CHATBOT]`/`[真人]` 標記所屬階段，
// 段內輪次以 ` ⏎ ` 分隔、輪次起首 `[ROLE]:` 標記發話角色；解析成輪次陣列（含所屬段落）供列表
// 結構化渲染；非對話形狀（無任何角色前綴）回 null 由呼叫端 fallback 原樣全文。

/** 對話段落（機器人階段／真人客服階段；無法辨識段落標記時為空字串）。 */
export type DialogueSegment = 'chatbot' | 'human' | '';

/** 對話單輪（`[ROLE]:` 前綴解析產物）。 */
export interface DialogueTurn {
  /** 該輪所屬段落。 */
  segment: DialogueSegment;
  /** 角色代碼（USER/BOT/KKDAY/SUP；空字串＝該輪無角色前綴的殘留文字）。 */
  role: string;
  /** 該輪文字（已去角色前綴與首尾空白；單欄展平時轉義的換行字面 `\n` 已還原為實際換行）。 */
  text: string;
}

/** 段落分隔（前後夾空白）。 */
const SEGMENT_SEP = ' ‖ ';
/** 段內輪次分隔（前後夾空白，取代原始換行）。 */
const TURN_SEP = ' ⏎ ';
/** 段落標記（`[CHATBOT]`/`[真人]`，段落文字起首）。 */
const SEGMENT_TAG_RE = /^\[(CHATBOT|真人)\]\s*/;
/** 段落標記 → DialogueSegment。 */
const SEGMENT_TAG_MAP: Record<string, DialogueSegment> = { CHATBOT: 'chatbot', 真人: 'human' };
/** 輪次角色前綴（如 `[USER]: `）；大寫字母/數字/底線代碼。 */
const TURN_PREFIX_RE = /^\[([A-Z][A-Z0-9_]*)\]:\s*/;

/**
 * 進線對話文字（conversation_full）→ 輪次列表：先以 ` ‖ ` 拆段（段落起首 `[CHATBOT]`/`[真人]`
 * 標記所屬階段），段內再以 ` ⏎ ` 拆輪次、行首 `[ROLE]:` 解析角色；輪次文字內因單欄展平而
 * 轉義的換行（字面 `\n`）還原為實際換行。整段無任何角色前綴（非對話形狀，如一般評論文字）
 * 回 null，呼叫端據此 fallback 原樣顯示。
 * @param content 反饋原文（conversation_full）
 * @returns 輪次陣列（空輪已濾除）；無角色前綴回 null
 */
export function parseDialogue(content: string): DialogueTurn[] | null {
  if (!content) return null;
  const turns: DialogueTurn[] = [];
  let sawRole = false;
  for (const rawSegment of content.split(SEGMENT_SEP)) {
    const trimmedSegment = rawSegment.trim();
    if (!trimmedSegment) continue;
    const tagMatch = SEGMENT_TAG_RE.exec(trimmedSegment);
    const segment: DialogueSegment = tagMatch ? SEGMENT_TAG_MAP[tagMatch[1]] : '';
    const body = tagMatch ? trimmedSegment.slice(tagMatch[0].length) : trimmedSegment;
    for (const rawTurn of body.split(TURN_SEP)) {
      const turnText = rawTurn.trim();
      if (!turnText) continue;
      const m = TURN_PREFIX_RE.exec(turnText);
      if (m) sawRole = true;
      const role = m ? m[1] : '';
      const text = (m ? turnText.slice(m[0].length) : turnText).replace(/\\n/g, '\n').trim();
      if (text) turns.push({ segment, role, text });
    }
  }
  if (!sawRole) return null;
  return turns;
}

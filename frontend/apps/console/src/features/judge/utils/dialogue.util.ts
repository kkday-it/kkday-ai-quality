// 進線對話解析（純函式）：conversations content 為 `[ROLE]: 文字` 逐行串接
// （chatbot_conversation + human_conversation 後端 merge_fields 換行合併），
// 解析成輪次陣列供列表結構化渲染；非對話形狀回 null 由呼叫端 fallback 原樣全文。

/** 對話單輪（`[ROLE]:` 前綴解析產物）。 */
export interface DialogueTurn {
  /** 角色代碼（USER/KKDAY/SUP/BOT；空字串＝首個前綴前的引言文字，渲染時不掛角色 tag）。 */
  role: string;
  /** 該輪文字（已去角色前綴與首尾空白；保留輪內換行）。 */
  text: string;
}

/** 行首角色前綴（如 `[USER]: `）；大寫字母/數字/底線代碼。 */
const TURN_PREFIX_RE = /^\[([A-Z][A-Z0-9_]*)\]:\s*/;

/**
 * 進線對話文字 → 輪次列表：行首 `[ROLE]:` 開新輪，其後續行併入同輪；
 * 首個前綴之前的文字收為 role='' 的引言輪。整段無任何角色前綴（非對話形狀，
 * 如一般評論文字）回 null，呼叫端據此 fallback 原樣顯示。
 * @param content 反饋原文
 * @returns 輪次陣列（空輪已濾除）；無角色前綴回 null
 */
export function parseDialogue(content: string): DialogueTurn[] | null {
  if (!content) return null;
  const turns: DialogueTurn[] = [];
  let current: DialogueTurn | null = null;
  let sawRole = false;
  for (const line of content.split('\n')) {
    const m = TURN_PREFIX_RE.exec(line);
    if (m) {
      sawRole = true;
      if (current) turns.push(current);
      current = { role: m[1], text: line.slice(m[0].length) };
    } else if (current) {
      current.text += `\n${line}`;
    } else {
      current = { role: '', text: line }; // 首個前綴前的引言
    }
  }
  if (current) turns.push(current);
  if (!sawRole) return null;
  return turns.map((t) => ({ role: t.role, text: t.text.trim() })).filter((t) => t.text.length > 0);
}

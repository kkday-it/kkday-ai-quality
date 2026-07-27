import { describe, expect, it } from 'vitest';
import { parseDialogue } from './dialogue.util';

describe('parseDialogue', () => {
  it('段內 [ROLE]: 前綴以 ⏎ 分隔逐輪拆解（去前綴、trim；無段落標記時 segment 為空字串）', () => {
    expect(parseDialogue('[USER]: 你好 ⏎ [KKDAY]: 您好，很高興為您服務')).toEqual([
      { segment: '', role: 'USER', text: '你好' },
      { segment: '', role: 'KKDAY', text: '您好，很高興為您服務' },
    ]);
  });

  it('段落標記 [CHATBOT]/[真人] + ‖ 分隔，各段輪次標上對應 segment', () => {
    const content =
      ' [CHATBOT] [USER]: live agent please ⏎ [BOT]: Transferring you... ‖ [真人] [KKDAY]: 您好';
    expect(parseDialogue(content)).toEqual([
      { segment: 'chatbot', role: 'USER', text: 'live agent please' },
      { segment: 'chatbot', role: 'BOT', text: 'Transferring you...' },
      { segment: 'human', role: 'KKDAY', text: '您好' },
    ]);
  });

  it('輪次文字內轉義換行字面 \\n（單欄展平的原始換行）還原為實際換行', () => {
    expect(parseDialogue('[KKDAY]: 已為您轉接\\n\\n預計等待 30 分鐘')).toEqual([
      { segment: '', role: 'KKDAY', text: '已為您轉接\n\n預計等待 30 分鐘' },
    ]);
  });

  it('SUP 角色與同段落多輪', () => {
    expect(parseDialogue('[USER]: 喔喔那就好 ⏎ [USER]: 謝謝你 ⏎ [SUP]: 不客氣')).toEqual([
      { segment: '', role: 'USER', text: '喔喔那就好' },
      { segment: '', role: 'USER', text: '謝謝你' },
      { segment: '', role: 'SUP', text: '不客氣' },
    ]);
  });

  it('無任何角色前綴（一般評論文字）→ null 供呼叫端 fallback 原樣', () => {
    expect(parseDialogue('行程很棒，導遊很專業！下次還會再來')).toBeNull();
    expect(parseDialogue('')).toBeNull();
  });

  it('非行首 / 非大寫代碼的 [x]: 不誤判為角色', () => {
    expect(parseDialogue('時間 [note]: 補充說明')).toBeNull();
    expect(parseDialogue('前言 [USER]: 內文非行首不開輪')).toBeNull();
  });
});

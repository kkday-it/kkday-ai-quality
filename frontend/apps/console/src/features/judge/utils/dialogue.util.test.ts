import { describe, expect, it } from 'vitest';
import { parseDialogue } from './dialogue.util';

describe('parseDialogue', () => {
  it('[ROLE]: 行首前綴 → 逐輪拆解（去前綴、trim）', () => {
    expect(parseDialogue('[USER]: 你好\n[KKDAY]: 您好，很高興為您服務')).toEqual([
      { role: 'USER', text: '你好' },
      { role: 'KKDAY', text: '您好，很高興為您服務' },
    ]);
  });

  it('續行併入同輪（保留輪內換行）', () => {
    expect(parseDialogue('[SUP]: Dear guest,\nMay I have more details?\n[USER]: Sure')).toEqual([
      { role: 'SUP', text: 'Dear guest,\nMay I have more details?' },
      { role: 'USER', text: 'Sure' },
    ]);
  });

  it('首個前綴前的引言 → role 空字串輪；空輪濾除', () => {
    expect(parseDialogue('系統轉接紀錄\n[BOT]: 您好\n[USER]:   \n[KKDAY]: 收到')).toEqual([
      { role: '', text: '系統轉接紀錄' },
      { role: 'BOT', text: '您好' },
      { role: 'KKDAY', text: '收到' }, // [USER] 空白輪已濾除
    ]);
  });

  it('無任何角色前綴（一般評論文字）→ null 供呼叫端 fallback 原樣', () => {
    expect(parseDialogue('行程很棒，導遊很專業！\n下次還會再來')).toBeNull();
    expect(parseDialogue('')).toBeNull();
  });

  it('非行首 / 非大寫代碼的 [x]: 不誤判為角色', () => {
    expect(parseDialogue('時間 [note]: 補充說明')).toBeNull();
    expect(parseDialogue('前言 [USER]: 內文非行首不開輪')).toBeNull();
  });
});

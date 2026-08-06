import { describe, expect, it } from 'vitest';
import { SYSTEM_ACTOR, formatActor } from './actor.util';

describe('formatActor', () => {
  it('系統標記原樣顯示，不做中文化（全站與 DB／API 同一個字面值）', () => {
    expect(formatActor(SYSTEM_ACTOR)).toBe('system');
  });

  it('空值正規化為 system（舊資料的 NULL / 空字串語義上就是系統操作）', () => {
    expect(formatActor('')).toBe(SYSTEM_ACTOR);
    expect(formatActor('   ')).toBe(SYSTEM_ACTOR);
    expect(formatActor(null)).toBe(SYSTEM_ACTOR);
    expect(formatActor(undefined)).toBe(SYSTEM_ACTOR);
  });

  it('真實 email 原樣顯示（be2 SSO 接入後的身分）', () => {
    expect(formatActor('alvin.bian@kkday.com')).toBe('alvin.bian@kkday.com');
  });
});

// @vitest-environment jsdom
//
// （全域 vitest 環境是 node——本檔要真的 localStorage 才驗得到持久化語義，故以 docblock
// 單檔切 jsdom，不動全域設定去影響其餘 138 支純函式測試的啟動成本。）
//
// 人工評判案例本地 store 的行為與**列表列形狀**護欄。
//
// 存在的理由：案例從 DB 搬到 localStorage 時，列表列少了兩個原本由後端算好的衍生欄
// （`conversation_chars` / `conversation_preview`），而抽屜的展開列直接讀它們——
// 結果是 `undefined.toLocaleString()` 執行期爆掉、**整個抽屜渲染失敗、按鈕按了沒反應**
// （2026-08-05 實際發生）。
//
// ⚠️ 這類漏欄 `vue-tsc` 天生抓不到：Arco 表格 slot 的 `record` 是 `TableData`，
// 帶 `[name: string]: any`，任何欄位存取都合法。所以護欄只能放在這裡。
import { beforeEach, describe, expect, it } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';
import {
  MAX_CASES,
  PREVIEW_CHARS,
  usePromptReviewCasesStore,
  type PromptReviewCaseRow,
} from './promptReviewCases.store';

/** 抽屜列表（含展開列）實際會讀到的欄位。少一個就是執行期 crash，不是型別錯誤。 */
const ROW_FIELDS_USED_BY_UI: Array<keyof PromptReviewCaseRow> = [
  'id',
  'created_at',
  'comment',
  'prompt_version',
  'model',
  'corrections',
  'conversation_chars',
  'conversation_preview',
];

function seed(conversation: string, extra: Partial<PromptReviewCaseRow> = {}) {
  return usePromptReviewCasesStore().add({
    conversation,
    ai_output: { L1: 'a' },
    corrections: { L1: 'b' },
    confirmed: ['L2'],
    comment: '',
    prompt_version: 'release-v2',
    model: 'gpt-5.4-mini',
    ...extra,
  });
}

describe('promptReviewCases store', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    localStorage.clear();
  });

  it('列表列帶齊 UI 會讀到的每一個欄位（防再次整個抽屜炸掉）', () => {
    seed('哈囉');
    const row = usePromptReviewCasesStore().sorted[0];
    for (const f of ROW_FIELDS_USED_BY_UI) {
      expect(row[f], `列表列缺欄位 ${String(f)}`).not.toBeUndefined();
    }
  });

  it('對話字數與預覽由全文衍生；超長才截斷並加省略號', () => {
    const long = 'x'.repeat(PREVIEW_CHARS + 50);
    seed(long);
    seed('短的');
    const [newest, oldest] = usePromptReviewCasesStore().sorted;
    expect(newest.conversation_chars).toBe(2);
    expect(newest.conversation_preview).toBe('短的');
    expect(oldest.conversation_chars).toBe(PREVIEW_CHARS + 50);
    expect(oldest.conversation_preview).toHaveLength(PREVIEW_CHARS + 1); // 200 字 + …
    expect(oldest.conversation_preview.endsWith('…')).toBe(true);
  });

  it('排序是新→舊（與原後端列表同口徑）', () => {
    const a = seed('第一則');
    const b = seed('第二則');
    expect(usePromptReviewCasesStore().sorted.map((r) => r.id)).toEqual([b, a]);
  });

  it('payloads 只送契約內的欄——衍生顯示欄與本地欄位不外流到後端', () => {
    const id = seed('哈囉');
    const [p] = usePromptReviewCasesStore().payloads([id]);
    expect(Object.keys(p).sort()).toEqual(
      ['ai_output', 'comment', 'confirmed', 'corrections', 'conversation', 'id'].sort(),
    );
  });

  it('刪除回報是否真的刪到；id 不重用，避免與勾選狀態撞號', () => {
    const a = seed('一');
    const b = seed('二');
    const store = usePromptReviewCasesStore();
    expect(store.remove(a)).toBe(true);
    expect(store.remove(a)).toBe(false);
    expect(seed('三')).toBe(b + 1);
  });

  it('撞到上限要拋錯，不能靜默寫不進去', () => {
    const store = usePromptReviewCasesStore();
    for (let i = 0; i < MAX_CASES; i += 1) seed(`第 ${i} 則`);
    expect(() => seed('滿了')).toThrowError(/上限/);
    expect(store.cases).toHaveLength(MAX_CASES);
  });
});

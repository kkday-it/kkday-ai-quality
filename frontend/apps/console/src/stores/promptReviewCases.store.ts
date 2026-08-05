// 人工評判案例庫（**存於瀏覽器本地**，不落 DB）。
//
// 2026-08-04 由後端 `prompt_debug_reviews` 表搬到這裡：案例是**個人調試用的暫存語料**，
// 不是團隊共享資產——落庫只是多一張表與一套 CRUD。改寫／回歸改為由請求整包帶上案例內容，
// 後端純運算不持久化。
//
// ⚠️ 代價（刻意接受）：案例不跨人、不跨裝置，清瀏覽器資料就沒了。
// ⚠️ localStorage 有 ~5MB 上限，而對話全文動輒上萬字——`MAX_CASES` 是為此設的硬上限，
//    超過時擋下新增並提示，避免寫入失敗變成「按了存檔卻沒存到」的靜默失敗。
import { computed } from 'vue';
import { useLocalStorage } from '@vueuse/core';
import { defineStore } from 'pinia';
import type { PromptDebugCasePayload } from '@/api';

// 用 type alias 而非 interface 是有意的：案例會直接餵給 `TableLayout` 的 `data`
// （`Record<string, unknown>[]`），而 TS 只對物件型別字面量給隱式索引簽名，interface 不給。
/** 一則人工評判案例（形狀與後端 `PromptDebugCaseIn` 對齊）。 */
export type PromptReviewCase = {
  /** 本地遞增 id（僅供列表勾選與進度回報對應，不具跨裝置意義）。 */
  id: number;
  /** 當時的調試文本原文（完整 IM session）。 */
  conversation: string;
  /** AI 判定的全部欄位（原樣保留）。 */
  ai_output: Record<string, unknown>;
  /** 人標的正解 `{欄名: 正解值}`；只含被標錯的欄，`{}`＝全欄皆對（正例，回歸時防過度矯正）。 */
  corrections: Record<string, unknown>;
  /** 人明確標「對」的欄名；回歸時這些欄不准變。兩者都沒出現的欄＝沒看過，不計分。 */
  confirmed: string[];
  /** 人寫的整體修改建議。 */
  comment: string;
  /** 當時的 Prompt 版本名；空＝送出前臨時編輯過。 */
  prompt_version: string;
  /** 當時使用的模型。 */
  model: string;
  /** 建立時間（ISO 8601）。 */
  created_at: string;
};

/**
 * 列表列＝案例本體 + 兩個**衍生顯示欄**。
 *
 * 這兩欄原本由後端算好再回傳（列表端點只給前 200 字預覽 + 全文字數，避免整份對話上 wire）。
 * 案例改存本地後全文本來就在手上，衍生就搬到這裡——但**必須真的補上**：表格展開列直接讀
 * `record.conversation_chars` / `record.conversation_preview`，少了就是
 * `undefined.toLocaleString()` 執行期爆掉、整個抽屜渲染失敗（2026-08-05 實際發生）。
 *
 * ⚠️ vue-tsc 抓不到這類漏欄：Arco 表格 slot 的 `record` 型別是 `TableData`，帶
 * `[name: string]: any` 索引簽名，任何欄位存取都合法。防線只能靠這個具名型別 + 單測。
 */
export type PromptReviewCaseRow = PromptReviewCase & {
  /** 對話全文字數。 */
  conversation_chars: number;
  /** 對話原文前 `PREVIEW_CHARS` 字（展開列用；塞全文會把表撐爆）。 */
  conversation_preview: string;
};

/** 本地案例數上限——localStorage ~5MB，對話全文動輒上萬字，超過會寫入失敗。 */
export const MAX_CASES = 200;

/** 展開列的對話預覽長度（與原後端列表端點同口徑）。 */
export const PREVIEW_CHARS = 200;

export const usePromptReviewCasesStore = defineStore('promptReviewCases', () => {
  const cases = useLocalStorage<PromptReviewCase[]>('aiq.promptReviewCases', []);

  /** 新→舊（與原本後端列表順序一致，消費端零改動）。 */
  const sorted = computed<PromptReviewCaseRow[]>(() =>
    [...cases.value]
      .sort((a, b) => b.id - a.id)
      .map((c) => {
        const text = c.conversation ?? '';
        return {
          ...c,
          conversation_chars: text.length,
          conversation_preview:
            text.length > PREVIEW_CHARS ? `${text.slice(0, PREVIEW_CHARS)}…` : text,
        };
      }),
  );

  /** 下一個本地 id（取現有最大值 +1，刪除後不重用避免與勾選狀態撞號）。 */
  function nextId(): number {
    return cases.value.reduce((mx, c) => Math.max(mx, c.id), 0) + 1;
  }

  /**
   * 新增一則案例。
   * @returns 新案例的本地 id。
   * @throws {Error} 已達 `MAX_CASES` 上限（呼叫端須顯示提示，勿靜默吞掉）。
   */
  function add(payload: Omit<PromptReviewCase, 'id' | 'created_at'>): number {
    if (cases.value.length >= MAX_CASES) {
      throw new Error(`本地案例已達上限 ${MAX_CASES} 則，請先刪除舊案例`);
    }
    const id = nextId();
    cases.value = [...cases.value, { ...payload, id, created_at: new Date().toISOString() }];
    return id;
  }

  /** 刪除一則；回傳是否真的刪到（不存在回 false，比照原後端 404 語義）。 */
  function remove(id: number): boolean {
    const before = cases.value.length;
    cases.value = cases.value.filter((c) => c.id !== id);
    return cases.value.length < before;
  }

  /** 依 id 取多則（供改寫／回歸整包送上後端）。順序依傳入的 ids。 */
  function pick(ids: number[]): PromptReviewCase[] {
    const byId = new Map(cases.value.map((c) => [c.id, c]));
    return ids.map((i) => byId.get(i)).filter((c): c is PromptReviewCase => !!c);
  }

  /**
   * 依 id 取出並投影成端點契約形狀（改寫／回歸共用）。
   * 只送契約內的欄——本地欄位（model / prompt_version / created_at）純供列表顯示，不外流。
   */
  function payloads(ids: number[]): PromptDebugCasePayload[] {
    return pick(ids).map((c) => ({
      id: c.id,
      conversation: c.conversation,
      ai_output: c.ai_output,
      corrections: c.corrections,
      confirmed: c.confirmed,
      comment: c.comment,
    }));
  }

  return { cases, sorted, add, remove, pick, payloads, MAX_CASES };
});

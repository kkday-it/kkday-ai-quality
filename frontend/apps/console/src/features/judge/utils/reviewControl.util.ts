// 人工評判「填正解」控件推導：從後端回的 output_schema 反推每個欄位該用什麼控件、什麼值域。
// 刻意不在前端手抄一份欄位型別表——受控 enum（category / likely_cause / theme…）會隨分類 SSOT
// 演進，手抄必 drift；schema 是後端從 SSOT 派生的唯一真相源，照著它長控件就永遠對得上。

/** 單一欄位的 JSON Schema 片段（只取推導控件會用到的鍵）。 */
interface FieldSchema {
  type?: string;
  enum?: unknown[];
  minimum?: number;
  maximum?: number;
  minLength?: number;
  maxLength?: number;
  maxItems?: number;
  items?: { minLength?: number; maxLength?: number };
}

/** 填正解時要渲染的控件；kind 決定元件、其餘欄位是該控件的值域約束。 */
export type ReviewControl =
  | { kind: 'select'; options: string[] }
  | { kind: 'switch' }
  | { kind: 'radio'; options: number[] }
  | { kind: 'tags'; maxItems?: number; itemMin?: number; itemMax?: number }
  | { kind: 'number'; min?: number; max?: number }
  | { kind: 'textarea'; minLength?: number; maxLength?: number };

/** 整數欄改用分段按鈕（而非數字輸入）的檔位數上限；超過就退回數字輸入免得排成一長列。 */
const MAX_RADIO_OPTIONS = 10;

/** 從 output_schema 取某欄的 schema 片段；取不到回空物件（呼叫端會落到預設控件）。 */
function fieldSchemaOf(schema: Record<string, unknown> | undefined, key: string): FieldSchema {
  const properties = (schema?.properties ?? {}) as Record<string, FieldSchema | undefined>;
  return properties[key] ?? {};
}

/**
 * 推導某欄位填正解時該用的控件。
 * @param schema 後端 `PromptDebugDefaults.output_schema` 全文
 * @param key 欄位鍵（theme / category / urgency…）
 * @returns 控件種類與值域；schema 認不出型別時退回多行文字框（永遠填得進去，不會卡住評判）
 */
export function controlForField(
  schema: Record<string, unknown> | undefined,
  key: string,
): ReviewControl {
  const field = fieldSchemaOf(schema, key);

  if (Array.isArray(field.enum)) {
    return { kind: 'select', options: field.enum.map(String) };
  }
  if (field.type === 'boolean') return { kind: 'switch' };
  if (field.type === 'integer') {
    const { minimum, maximum } = field;
    // 小值域整數（urgency 1–5）走分段按鈕：一眼看完全部檔位，比數字輸入好按也不會填出界
    if (
      minimum !== undefined &&
      maximum !== undefined &&
      maximum - minimum + 1 <= MAX_RADIO_OPTIONS
    ) {
      const options = Array.from({ length: maximum - minimum + 1 }, (_, i) => minimum + i);
      return { kind: 'radio', options };
    }
    return { kind: 'number', min: minimum, max: maximum };
  }
  if (field.type === 'number') return { kind: 'number', min: field.minimum, max: field.maximum };
  if (field.type === 'array') {
    return {
      kind: 'tags',
      maxItems: field.maxItems,
      itemMin: field.items?.minLength,
      itemMax: field.items?.maxLength,
    };
  }
  return { kind: 'textarea', minLength: field.minLength, maxLength: field.maxLength };
}

/** 單一下層欄位的級聯規則（後端 `output_cascade` 的一項）。 */
export interface CascadeRule {
  /** 上層欄位鍵（category 的父是 theme、likely_cause 的父是 category）。 */
  parent: string;
  /** 上層值 → 該分支底下的可選清單。 */
  options_by_parent: Record<string, string[]>;
}

/** 後端 `output_cascade` 全文：下層欄位鍵 → 級聯規則。 */
export type OutputCascade = Record<string, CascadeRule>;

/**
 * 取某欄在指定上層值底下的可選清單。
 *
 * 用途是把「填正解」的下拉限縮到已選上層的分支——schema enum 是攤平的全域值域，
 * 直接用會讓人挑得到 theme 與 category 不相配的組合（`validate_result` 雖然擋得下來，
 * 但那已經是存檔當下，回頭改成本高）。
 *
 * @param cascade 後端 `output_cascade`；未提供＝後端還沒回來或該版本沒有級聯資料
 * @param key 要限縮的欄位鍵
 * @param parentValue 上層欄位當前有效的值
 * @returns 該分支底下的可選清單；此欄無級聯規則、或上層值不在表內（人還沒選 / 值已過期）時回 `null` 表示不限縮
 */
export function optionsUnderParent(
  cascade: OutputCascade | undefined,
  key: string,
  parentValue: unknown,
): string[] | null {
  const rule = cascade?.[key];
  if (!rule || typeof parentValue !== 'string') return null;
  return rule.options_by_parent[parentValue] ?? null;
}

/**
 * 標錯某欄時，正解輸入框的預填值。
 *
 * 以 AI 判的值為底而非留白：多數誤判只錯一個維度（如 category 對、likely_cause 錯），
 * 讓人改一個字比整欄重打快，也避免手滑存進空值當「正解」。
 *
 * @param control 該欄的控件（決定型別該長什麼樣）
 * @param current AI 當時判的值
 * @returns 型別與控件相符的初始值
 */
export function defaultCorrection(control: ReviewControl, current: unknown): unknown {
  switch (control.kind) {
    case 'switch':
      // 標錯＝人不同意 AI 的布林判定，直接翻面就是他要的答案
      return typeof current === 'boolean' ? !current : false;
    case 'radio':
      return typeof current === 'number' ? current : control.options[0];
    case 'number':
      return typeof current === 'number' ? current : (control.min ?? 0);
    case 'tags':
      return Array.isArray(current) ? [...current.map(String)] : [];
    case 'select':
      return typeof current === 'string' && control.options.includes(current) ? current : '';
    default:
      return current === null || current === undefined ? '' : String(current);
  }
}

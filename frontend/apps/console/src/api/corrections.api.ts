/**
 * 人工糾正歸因 + 待審建議 + 判決值域（全 POST，遵循本專案零 PUT/PATCH/DELETE 慣例）。
 *
 * 糾正是 `attribution_tbl` 唯一的人工寫入路徑，也是「人工託管」的入口：一旦改過，該則反饋的
 * 重新初判就不再覆蓋現值，AI 的新結論改走待審建議。
 */
import type { Attribution } from '@/features/judge/constants';
import { BASE, j } from './http.api';

/** 糾正政策（可改欄白名單 + 理由門檻）；與後端寫入白名單同讀一份 config，避免兩邊漂移。 */
export interface CorrectionPolicy {
  editable_fields: string[];
  reason_min_length: number;
  reason_max_length: number;
}

/** 糾正／新增／刪除／還原／確認的統一回傳。 */
export interface CorrectionResult {
  attribution: Attribution;
  changed?: Record<string, [unknown, unknown]>;
  confirmed_fields?: string[];
}

/** 一條待審建議：`current`（人工現值）與 `proposed`（LLM 新值）**兩側同形**，對比 UI 共用渲染。 */
export interface SuggestionItem {
  suggestion_oid: number;
  change_type: 'replace' | 'add' | 'remove';
  attribution_oid: number | null;
  current: Attribution | null;
  proposed: Attribution;
}

export interface PendingSuggestions {
  batch_id: string | null;
  model: string | null;
  created_at: string | null;
  items: SuggestionItem[];
}

/** 判決值域主檔的單一選項。 */
export interface DimensionItem {
  attribution_dimension_oid?: number;
  dimension_code: string;
  item_code: string;
  item_label: string;
  item_desc?: string | null;
  sort_order?: number;
  is_active?: boolean;
}

const post = <T>(path: string, body: unknown): Promise<T> =>
  j(`${BASE}${path}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

/** 糾正政策（前端糾正抽屜據此決定表單長什麼樣）。 */
export const getCorrectionPolicy = (): Promise<CorrectionPolicy> =>
  j(`${BASE}/attributions/correction-policy`);

/** 修改一條 AI 歸因的分類／傾向（該反饋自此進入人工託管）。 */
export const correctAttribution = (body: {
  source: string;
  source_id: string;
  attribution_oid: number;
  changes: Record<string, unknown>;
  reason: string;
}): Promise<CorrectionResult> => post('/attributions/correct', body);

/** 人工新增一條 AI 漏掉的歸因。 */
export const createAttribution = (body: {
  source: string;
  source_id: string;
  values: Record<string, unknown>;
  reason: string;
}): Promise<CorrectionResult> => post('/attributions/create', body);

/** 標記為 AI 誤判（tombstone：列保留佔住自然鍵，防重新初判悄悄復活）。 */
export const deleteAttribution = (body: {
  source: string;
  source_id: string;
  attribution_oid: number;
  reason: string;
}): Promise<CorrectionResult> => post('/attributions/delete', body);

/** 還原被標記為誤判的歸因。 */
export const restoreAttribution = (body: {
  source: string;
  source_id: string;
  attribution_oid: number;
  reason: string;
}): Promise<CorrectionResult> => post('/attributions/restore', body);

/** 複審確認 AI 判對（待複審的出口）；不觸發人工託管閂鎖。 */
export const confirmAttribution = (body: {
  source: string;
  source_id: string;
  attribution_oid: number;
  confirmed_fields?: string[];
  note?: string;
}): Promise<CorrectionResult> => post('/attributions/confirm', body);

/**
 * 糾正工作台的資料源：一則反饋的**全部**歸因。
 *
 * `deleted` 是人工標記為 AI 誤判的 tombstone——列表與所有統計都看不到它們（讀取層 chokepoint
 * 一律排除），但工作台必須看得到，否則「還原誤判」沒有入口。
 *
 * 形狀是兩個陣列而非「每列帶一個 is_deleted 旗標」：後者會讓所有既有消費端
 * （列表 rows[].attributions、待審建議清單）平白多拿一個恆為 false 的欄。
 */
export interface RecordAttributions {
  live: Attribution[];
  deleted: Attribution[];
  /** 已進入人工託管（重新初判不再覆蓋現值，改走待審建議）。 */
  human_managed: boolean;
  /** 待處理的 LLM 建議數。 */
  suggestion_count: number;
}

/** 取一則反饋的全部歸因（含 tombstone）＋託管狀態＋待審建議數。 */
export const getRecordAttributions = (
  source: string,
  sourceId: string,
): Promise<RecordAttributions> =>
  j(`${BASE}/attributions?source=${encodeURIComponent(source)}&source_id=${encodeURIComponent(sourceId)}`);

/**
 * 互換同一則反饋內兩條歸因的面向（單一交易，兩條同時生效）。
 *
 * 存在的理由：「AI 把兩個面向的內容寫反了」在逐條提交下是死結——先改哪一條都會撞上另一條佔著的
 * 面向。沒有這個端點，使用者只能走「先改成第三個暫時面向 → 換另一條 → 再改回來」的三步。
 * tombstone 不參與（要換先還原），後端回 409。
 */
export const swapAttributionSlots = (body: {
  source: string;
  source_id: string;
  attribution_oid_a: number;
  attribution_oid_b: number;
  reason: string;
}): Promise<{ attributions: Attribution[]; changed: Record<string, unknown> }> =>
  post('/attributions/swap', body);

/** 某則反饋的待審 LLM 建議。 */
export const getSuggestions = (source: string, sourceId: string): Promise<PendingSuggestions> =>
  j(`${BASE}/attribution-suggestions?source=${encodeURIComponent(source)}&source_id=${encodeURIComponent(sourceId)}`);

/** 採納／駁回建議 → {applied, rejected, remaining}；batch 過期回 409 要求重新載入。 */
export const resolveSuggestions = (body: {
  source: string;
  source_id: string;
  batch_id: string;
  decisions: { suggestion_oid: number; decision: 'accept' | 'reject' }[];
  reason?: string;
}): Promise<{ applied: number; rejected: number; remaining: number }> =>
  post('/attribution-suggestions/resolve', body);

/** 值域四軸（責任方／嚴重度／建議行動／備註類型）。 */
export const getDimensions = (includeInactive = false): Promise<Record<string, DimensionItem[]>> =>
  j(`${BASE}/attribution-dimensions${includeInactive ? '?include_inactive=true' : ''}`);

/** 新增或更新單一值域項（無刪除端點——停用走 is_active=false）。 */
export const saveDimensionItem = (body: DimensionItem): Promise<DimensionItem> =>
  post('/attribution-dimensions/save', body);

/** 重寫某軸的顯示順序。 */
export const reorderDimension = (body: { dimension_code: string; item_codes: string[] }): Promise<{ updated: number }> =>
  post('/attribution-dimensions/reorder', body);

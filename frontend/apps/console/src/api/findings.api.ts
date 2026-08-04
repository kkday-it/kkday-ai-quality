// Findings 領域 API：歸因分類級聯樹、評論級歸因歷史與備註。
//
// 備註一律是**評論級**（`attribution-history/notes`，綁 (source, source_id)）而非歸因級：
// 歸因列每次重新初判都會整批換掉（先刪後插），綁在 attribution_oid 上的東西一重判就成孤兒。
import { BASE, JSON_HEADERS, j } from './http.api';

/** 歸因分類級聯節點（巢狀）：value＝L1 域 code 或 L2 面向 C-code。 */
export interface CascadeNode {
  value: string;
  label: string;
  children?: CascadeNode[];
}

/** 取歸因分類級聯樹（L1→L2）供 a-cascader 選項（歸因列表篩選選域與面向）。 */
export const getTaxonomyCascade = (): Promise<CascadeNode[]> =>
  j<CascadeNode[]>(`${BASE}/findings/taxonomy-cascade`);

/** 歸因歷史事件（評論級時間軸一項；kind 決定哪些欄位有值）。 */
export interface AttributionHistoryEntry {
  id: number;
  source: string;
  source_id: string;
  /**
   * 事件類型：`prejudge`（初判快照）/ `note`（評論級備註）/ `failure`（初判失敗留痕）。
   *
   * 這三種是後端 `_USER_VISIBLE_KINDS` 白名單保證的完整集合——內部遙測事件（如 router_shadow）
   * 在查詢層就被擋掉，不會流到這裡。要新增使用者可見的事件型別，必須**後端白名單與此 union
   * 同時加**，並補上 `AttributionHistoryDrawer` 的渲染分支；只加一邊的話，該事件不是看不到
   * （漏後端）就是掉進 `v-else` 兜底被渲染成 author/content 皆空的灰色「備註」（漏前端，
   * `failure` 曾這樣假冒 390 筆）。
   */
  kind: 'prejudge' | 'note' | 'failure';
  /** 初判模型（kind=prejudge；stub 同 attributions.model 語意）。 */
  model?: string | null;
  /** 事件細節：prejudge＝{model}（回填列 {backfilled:true}）；failure＝{error}。 */
  params?: Record<string, unknown> | null;
  /** 初判快照（kind=prejudge；每筆形狀近 Attribution：l1-l2/傾向/情緒分/信心/內容）。 */
  attributions?: Record<string, unknown>[] | null;
  job_id?: string | null;
  triggered_by?: string | null;
  /** 備註人（kind=note）。 */
  author?: string | null;
  /** 備註內容（kind=note）。 */
  content?: string | null;
  created_at: string | null;
}

/** 取某則評論的歸因歷史時間軸（舊到新；prejudge/note/failure 三類事件混排）。 */
export const getAttributionHistory = (
  source: string,
  sourceId: string,
): Promise<AttributionHistoryEntry[]> => {
  const q = new URLSearchParams({ source, source_id: sourceId });
  return j<AttributionHistoryEntry[]>(`${BASE}/attribution-history?${q.toString()}`);
};

/** 為某則評論新增一則評論級備註（歸因歷史時間軸內）。 */
export const addAttributionHistoryNote = (
  source: string,
  sourceId: string,
  content: string,
): Promise<AttributionHistoryEntry> =>
  j<AttributionHistoryEntry>(`${BASE}/attribution-history/notes`, {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ source, source_id: sourceId, content }),
  });

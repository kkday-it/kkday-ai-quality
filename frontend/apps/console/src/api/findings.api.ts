// Findings 領域 API：歸因分類級聯樹、反饋時間軸與備註。
//
// 備註有兩個層級（2026-08-07 起）：**整則備註**（綁 (source, source_id)）與**面向備註**
// （再綁 (l1_code, l2_code)）。⚠️ 面向備註綁的是**面向**不是 `attribution_oid`——歸因列每次
// 重新初判都會整批換掉（先刪後插），綁流水號的東西一重判就成孤兒（2026-08-04 退役的
// finding_notes 表 8 列裡有 6 列正是這樣死的）。
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
  kind:
    | 'prejudge'
    | 'note'
    | 'failure'
    | 'correction'
    | 'review_confirm'
    | 'suggestion'
    | 'suggestion_resolved';
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

// ── 反饋備註（獨立表 attribution_note_lst；時間軸由後端合併回傳）─────────────────

/** 互動類型選項（值域主檔的 note_type 軸，業務可於設定 › 判決值域維護）。 */
export interface NoteType {
  item_code: string;
  item_label: string;
  item_desc: string | null;
}

/** 一則備註。`slot` 為 null＝整則備註；有值＝掛在該 L1›L2 面向上。 */
export interface AttributionNote {
  attribution_note_oid: number;
  source: string;
  source_id: string;
  slot: { l1_code: string; l2_code: string } | null;
  note_type: string;
  content: string;
  author: string | null;
  created_at: string | null;
}

/** 可選的互動類型（僅啟用項）。 */
export const getNoteTypes = (): Promise<NoteType[]> => j(`${BASE}/attribution-notes/types`);

/** 某則反饋的全部備註（舊到新）。時間軸另有合併版，見 getAttributionHistory。 */
export const listAttributionNotes = (
  source: string,
  sourceId: string,
): Promise<AttributionNote[]> =>
  j(`${BASE}/attribution-notes?source=${encodeURIComponent(source)}&source_id=${encodeURIComponent(sourceId)}`);

/**
 * 新增一則備註（append-only，寫出去不能改也不能撤回）。
 *
 * `l1Code` / `l2Code` 同時給＝面向備註（綁的是**面向**不是那一列歸因，故重新初判後仍在）；
 * 同時省略＝整則備註。只給其中一個回 422。
 */
export const addAttributionNote = (body: {
  source: string;
  source_id: string;
  note_type: string;
  content: string;
  l1_code?: string | null;
  l2_code?: string | null;
}): Promise<AttributionNote> =>
  j(`${BASE}/attribution-notes`, {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify(body),
  });

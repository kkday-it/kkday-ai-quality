// Findings 領域 API：狀態更新、備註、級聯樹。
import { BASE, JSON_HEADERS, j } from './http.api';

export const patchStatus = (findingId: string, status: string) =>
  j(`${BASE}/findings/${encodeURIComponent(findingId)}/verdict`, {
    method: 'PATCH',
    headers: JSON_HEADERS,
    body: JSON.stringify({ status }),
  });

/** 歸因分類級聯節點（巢狀）：value＝L1 域 code 或 L2 面向 C-code。 */
export interface CascadeNode {
  value: string;
  label: string;
  children?: CascadeNode[];
}

/** 取歸因分類級聯樹（L1→L2）供 a-cascader 選項（歸因列表篩選選域與面向）。 */
export const getTaxonomyCascade = (): Promise<CascadeNode[]> =>
  j<CascadeNode[]>(`${BASE}/findings/taxonomy-cascade`);

/** 歸因備註（append-only 歷史一則）。 */
export interface FindingNote {
  id: number;
  finding_id: string;
  author: string;
  content: string;
  created_at: string | null;
}

/** 取某條歸因的備註歷史（新到舊：備註人 / 時間 / 內容）。 */
export const getFindingNotes = (findingId: string): Promise<FindingNote[]> =>
  j<FindingNote[]>(`${BASE}/findings/${encodeURIComponent(findingId)}/notes`);

/** 為某條歸因新增一則備註（備註人由登入身分帶入、時間由後端補）。 */
export const addFindingNote = (findingId: string, content: string): Promise<FindingNote> =>
  j<FindingNote>(`${BASE}/findings/${encodeURIComponent(findingId)}/notes`, {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ content }),
  });

/**
 * 批量初判：對多則評論（sourceIds＝勾選的 source_id）的全部歸因設定 status
 * （confirmed/dismissed/new＝撤銷）；後端單交易逐筆 diff（同值冪等跳過）並記入歸因歷史。
 */
export const batchPatchStatus = (
  source: string,
  sourceIds: string[],
  status: string,
): Promise<{ status: string; updated: number; finding_ids: string[] }> =>
  j<{ status: string; updated: number; finding_ids: string[] }>(`${BASE}/findings/batch/verdict`, {
    method: 'PATCH',
    headers: JSON_HEADERS,
    body: JSON.stringify({ source, source_ids: sourceIds, status }),
  });

/** 歸因歷史事件（評論級時間軸一項；kind 決定有值欄位：judgment 快照 / status 轉移 / note 備註）。 */
export interface AttributionHistoryEntry {
  id: number;
  source: string;
  source_id: string;
  /** 事件類型：judgment（初判快照）/ status（判決轉移）/ note（評論級備註）。 */
  kind: 'prejudge' | 'verdict' | 'note';
  /** 初判模型（kind=judgment；stub 同 attributions.model 語意）。 */
  model?: string | null;
  /** 事件細節：judgment＝{model}（回填列 {backfilled:true}）；
   *  status＝{to, changes:[{finding_id, from}]}。 */
  params?: Record<string, unknown> | null;
  /** 初判快照（kind=judgment；每筆形狀近 Attribution：l1-l2/傾向/情緒分/信心/內容）。 */
  attributions?: Record<string, unknown>[] | null;
  result_digest?: string | null;
  job_id?: string | null;
  triggered_by?: string | null;
  /** 操作者/備註人（kind=status/note）。 */
  author?: string | null;
  /** 備註內容（kind=note）。 */
  content?: string | null;
  created_at: string | null;
}

/** 取某則評論的歸因歷史時間軸（新到舊；judgment/status/note 三類事件混排）。 */
export const getAttributionHistory = (
  source: string,
  sourceId: string,
): Promise<AttributionHistoryEntry[]> => {
  const q = new URLSearchParams({ source, source_id: sourceId });
  return j<AttributionHistoryEntry[]>(`${BASE}/attribution-history?${q.toString()}`);
};

/** 為某則評論新增一則評論級備註（歸因歷史時間軸內；與 finding 級備註並存）。 */
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

// Findings 領域 API：狀態更新、真值標註、備註、級聯樹。
import { BASE, JSON_HEADERS, j } from './http.api';

export const patchStatus = (findingId: string, status: string) =>
  j(`${BASE}/findings/${encodeURIComponent(findingId)}/status`, {
    method: 'PATCH',
    headers: JSON_HEADERS,
    body: JSON.stringify({ status }),
  });

/** 歸因分類級聯節點（巢狀）：value＝L1 域 code 或 L2/L3 的 C-code。 */
export interface CascadeNode {
  value: string;
  label: string;
  children?: CascadeNode[];
}

/** 取歸因分類級聯樹（L1→L2→L3）供標真值 a-cascader 選項。 */
export const getTaxonomyCascade = (): Promise<CascadeNode[]> =>
  j<CascadeNode[]>(`${BASE}/findings/taxonomy-cascade`);

/** 標真值把關評分結果：LLM 對提議真值的信心 + 與原判對比 + 是否需填理由。 */
export interface TrueLabelEval {
  finding_id: string;
  proposed_label: string;
  llm_confidence: number;
  original_confidence: number | null;
  delta: number | null;
  reason_llm: string;
  reason_required: boolean;
  threshold: number;
}

/** LLM 對「提議真值 vs 反饋原文」重判評分（標真值確認時跑）；回信心對比 + 是否需填理由。 */
export const evaluateTrueLabel = (
  findingId: string,
  proposedLabel: string,
): Promise<TrueLabelEval> =>
  j<TrueLabelEval>(`${BASE}/findings/${encodeURIComponent(findingId)}/true_label/evaluate`, {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ proposed_label: proposedLabel }),
  });

/**
 * 人工標註單筆歸因真值分類 true_label（null/空＝清除）；重判依 finding_id 保留。
 * reason＝LLM 信心明顯下降時的修改理由；llmConf＝標註當下 LLM 對真值的契合信心（audit + 後端把關）。
 */
export const updateTrueLabel = (
  findingId: string,
  trueLabel: string | null,
  opts: { reason?: string; llmConf?: number } = {},
) =>
  j(`${BASE}/findings/${encodeURIComponent(findingId)}/true_label`, {
    method: 'PATCH',
    headers: JSON_HEADERS,
    body: JSON.stringify({ true_label: trueLabel, reason: opts.reason, llm_conf: opts.llmConf }),
  });

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
 * 批量覆核：對多則評論（sourceIds＝勾選的 source_id）的全部歸因設定 status
 * （confirmed/dismissed/new＝撤銷）；後端單交易逐筆 diff（同值冪等跳過）並記入判決歷史。
 */
export const batchPatchStatus = (
  source: string,
  sourceIds: string[],
  status: string,
): Promise<{ status: string; updated: number; finding_ids: string[] }> =>
  j<{ status: string; updated: number; finding_ids: string[] }>(`${BASE}/findings/batch/status`, {
    method: 'PATCH',
    headers: JSON_HEADERS,
    body: JSON.stringify({ source, source_ids: sourceIds, status }),
  });

/** 判決歷史事件（評論級時間軸一項；kind 決定有值欄位：judgment 快照 / status 轉移 / note 備註）。 */
export interface JudgmentHistoryEntry {
  id: number;
  source: string;
  source_id: string;
  /** 事件類型：judgment（判決快照）/ status（覆核轉移）/ note（評論級備註）。 */
  kind: 'judgment' | 'status' | 'note';
  /** 判決模型（kind=judgment；stub/ensemble 同 judgments.model 語意）。 */
  model?: string | null;
  /** ensemble 各 voter 票（單模型 null；供多模型對比）。 */
  model_votes?: unknown;
  /** 事件細節：judgment＝{model, voter_models, ensemble_sample_rate}（回填列 {backfilled:true}）；
   *  status＝{to, changes:[{finding_id, from}]}。 */
  params?: Record<string, unknown> | null;
  /** 判決快照（kind=judgment；每筆形狀近 Attribution：l1-l3/傾向/情緒分/信心/內容）。 */
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

/** 取某則評論的判決歷史時間軸（新到舊；judgment/status/note 三類事件混排）。 */
export const getJudgmentHistory = (
  source: string,
  sourceId: string,
): Promise<JudgmentHistoryEntry[]> => {
  const q = new URLSearchParams({ source, source_id: sourceId });
  return j<JudgmentHistoryEntry[]>(`${BASE}/judgment-history?${q.toString()}`);
};

/** 為某則評論新增一則評論級備註（判決歷史時間軸內；與 finding 級備註並存）。 */
export const addJudgmentHistoryNote = (
  source: string,
  sourceId: string,
  content: string,
): Promise<JudgmentHistoryEntry> =>
  j<JudgmentHistoryEntry>(`${BASE}/judgment-history/notes`, {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ source, source_id: sourceId, content }),
  });

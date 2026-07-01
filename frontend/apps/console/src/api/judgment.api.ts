// 歸因領域 API：統一問題列表 + 即時匯總 + 初判歸因批量任務（選模型 + 進度輪詢）。
import { BASE, getToken, j } from './http.api';

/** 統一問題列表（intake + 歸因 join）。judged=true 僅已歸因。 */
export const getProblems = (params: {
  source?: string;
  judged?: boolean;
  polarity?: string;
  limit?: number;
  offset?: number;
} = {}) => {
  const q = new URLSearchParams();
  if (params.source) q.set('source', params.source);
  if (params.judged !== undefined) q.set('judged', String(params.judged));
  if (params.polarity) q.set('polarity', params.polarity);
  q.set('limit', String(params.limit ?? 2000));
  q.set('offset', String(params.offset ?? 0));
  return j(`${BASE}/problems?${q.toString()}`);
};

/** 問題即時匯總（來源 / 域 / 信心分層 分佈）。 */
export const getProblemsSummary = () => j(`${BASE}/problems/summary`);

/** 導出 CSV（POST·item_ids 放 body 避免 URL 過長 431）→ 回 Blob 供前端下載。 */
export const exportProblems = async (p: {
  source?: string;
  polarity?: string;
  judged?: boolean;
  item_ids?: string[];
}): Promise<Blob> => {
  const token = getToken();
  const res = await fetch(`${BASE}/problems/export`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(p),
  });
  if (!res.ok) throw new Error(`導出失敗 ${res.status}`);
  return res.blob();
};

/** 啟動初判歸因批量任務（選擇驅動：item_ids 複選 / scope=all 全部未判 + 指定模型）→ {job_id, total, model}。 */
export const startPrejudge = (body: {
  item_ids?: string[];
  source?: string;
  scope?: string;
  llm_config_id?: string;
}) =>
  j(`${BASE}/v1/judgment/prejudge`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

/** 查初判歸因任務進度 {status, total, processed, ok, failed, model, total_tokens, cost_usd}。 */
export const getPrejudgeStatus = (jobId: string) =>
  j(`${BASE}/v1/judgment/prejudge/status?job_id=${encodeURIComponent(jobId)}`);

/**
 * 歸因縱覽聚合（縱覽頁專用）：KPI + 傾向/L1域/判決/信心分層/星等 分布 + 月趨勢。
 * 一次取齊，避免前端全量 fetch 29k 列再算。
 * @param source 來源 code（省略＝全部來源）
 */
export const getAttributionOverview = (source?: string) => {
  const q = new URLSearchParams();
  if (source) q.set('source', source);
  return j(`${BASE}/problems/attribution_overview?${q.toString()}`);
};

/**
 * 某 L1 歸因域下的 L2/L3 細項分布（縱覽長條點擊下鑽·懶載）。
 * @param l1 L1 歸因域 code（如 'supplier'）
 * @param source 來源 code（省略＝全部來源）
 */
export const getAttributionBreakdown = (l1: string, source?: string) => {
  const q = new URLSearchParams({ l1 });
  if (source) q.set('source', source);
  return j(`${BASE}/problems/attribution_breakdown?${q.toString()}`);
};

// 訂單佐證領域 API：production 下單當時商品快照唯讀查詢（詳情抽屜 lazy fetch）。
import { BASE, j } from './http.api';

/** 佐證取數狀態（後端 qc_evidence.EvidenceResult.status 同值集）。 */
export type EvidenceStatus =
  'fetched' | 'cache_hit' | 'no_order_oid' | 'not_found' | 'degraded_unavailable' | 'error';

/** 訂單佐證回應：非成功時 data 為 null（缺佐證是常態非錯誤，後端不走 5xx）。 */
export interface OrderEvidence {
  status: EvidenceStatus;
  data: Record<string, unknown> | null;
}

/** 取單筆訂單佐證（下單當時商品快照投影；後端帶兩級快取 + 熔斷，重查便宜）。 */
export const getOrderEvidence = (orderOid: string | number): Promise<OrderEvidence> =>
  j<OrderEvidence>(`${BASE}/evidence/${orderOid}`);

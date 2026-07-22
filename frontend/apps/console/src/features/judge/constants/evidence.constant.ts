// 訂單佐證取數狀態的顯示字典（詳情抽屜佐證區塊 + 歸因留痕標籤共用）。
import type { EvidenceStatus } from '@/api';

/** 佐證狀態 → 中文標籤（鍵集＝EvidenceStatus；型別放寬 string 供歸因列 DTO 的 string 欄索引）。 */
export const EVIDENCE_STATUS_LABEL: Record<string, string> = {
  fetched: '已取得',
  cache_hit: '已取得（快取）',
  no_order_oid: '無訂單編號',
  not_found: '查無此單',
  degraded_unavailable: '佐證庫不可用（降級）',
  error: '查詢異常',
};

/** 佐證狀態 → Arco tag 色（成功綠 / 無單灰 / 降級橙 / 異常紅）；鍵集同上。 */
export const EVIDENCE_STATUS_COLOR: Record<string, string> = {
  fetched: 'green',
  cache_hit: 'green',
  no_order_oid: 'gray',
  not_found: 'gray',
  degraded_unavailable: 'orange',
  error: 'red',
};

/** 佐證狀態 → 空狀態說明文案（抽屜佐證區塊 data 為空時）。 */
export const EVIDENCE_EMPTY_TEXT: Record<EvidenceStatus, string> = {
  fetched: '—',
  cache_hit: '—',
  no_order_oid: '此反饋無訂單編號，無佐證可查',
  not_found: 'production 快照庫查無此訂單',
  degraded_unavailable: '佐證庫暫不可用（已降級）；可稍後重試',
  error: '佐證查詢異常，已記錄於後端日誌',
};

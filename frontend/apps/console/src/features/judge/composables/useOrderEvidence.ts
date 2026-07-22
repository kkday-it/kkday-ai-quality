/**
 * 訂單佐證 lazy fetch composable：三態（loading/error/result）+ session 級記憶體快取。
 *
 * 快取定位＝展示層輔助（同 session 重開抽屜零重撈）；資料源恆為 backend
 * `GET /api/evidence/{order_oid}`（後端自帶兩級 diskcache/single-flight/熔斷）——
 * 刻意不用 IndexedDB：判決在 backend 跑，瀏覽器存儲服務不了判決管線（D2 決策）。
 */
import { ref, type Ref } from 'vue';
import { getOrderEvidence, type OrderEvidence } from '@/api';

/** module 級快取（session 存活；key=order_oid）。 */
const _cache = new Map<string, OrderEvidence>();

/**
 * 佐證載入器（每個消費端各自一組三態；快取跨消費端共享）。
 *
 * @example
 * const { loading, error, result, load } = useOrderEvidence();
 * watch(visible, (v) => v && load(row?.order_oid));
 */
export function useOrderEvidence() {
  const loading = ref(false);
  const error = ref('');
  const result: Ref<OrderEvidence | null> = ref(null);

  /** 載入某訂單佐證；同 oid 命中記憶體快取零請求，force=true 繞過重撈。 */
  const load = async (
    orderOid: string | number | null | undefined,
    force = false,
  ): Promise<void> => {
    const oid = String(orderOid ?? '').trim();
    error.value = '';
    if (!oid) {
      result.value = { status: 'no_order_oid', data: null };
      return;
    }
    if (!force && _cache.has(oid)) {
      result.value = _cache.get(oid)!;
      return;
    }
    loading.value = true;
    result.value = null;
    try {
      const r = await getOrderEvidence(oid);
      _cache.set(oid, r);
      result.value = r;
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e);
    } finally {
      loading.value = false;
    }
  };

  return { loading, error, result, load };
}

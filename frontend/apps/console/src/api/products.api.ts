// Products 領域 API。
import { BASE, j } from './http.api';

/** 有 finding 的商品清單（PM 下拉用；每列含 prod_oid + 問題數 n）。 */
export const getProducts = (): Promise<Record<string, unknown>[]> =>
  j<Record<string, unknown>[]>(`${BASE}/products`);

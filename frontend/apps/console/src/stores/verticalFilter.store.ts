// 全局商品垂直分類篩選（兩層 SSOT，跨頁共享，狀態持久化跨 session）：
// ① 選項池 pool（規則配置頁配置）＝工具列篩選器可選的分類清單（總 list）；本身不直接篩資料。
// ② 篩選 filter（歸因列表工具列選中，限 pool 內）＝實際套用到 列表 / 縱覽 / 未判 / 初判 scope 的篩選。
// 嚴格限制：filter 永遠送後端展開成 CATEGORY 代碼做 IN 過濾（即使全選），使數據範圍鎖在選項池涵蓋的
// 分類內、排除未歸入任何分組的其他分類（用戶明確要求「不要獲取全部分類」）；僅選項池未初始化時不篩。
import { computed, ref } from 'vue';
import { defineStore } from 'pinia';
import { useLocalStorage } from '@vueuse/core';
import { getProductVerticalResolved } from '@/api';

export const useVerticalFilterStore = defineStore('verticalFilter', () => {
  /** 全部可能分組（來自 config/product_vertical；不持久化，每 session 由 loadOptions 補齊）。 */
  const allOptions = ref<string[]>([]);
  /** 選項池：規則配置頁配置的可選分類（＝工具列篩選器選項）；空＝尚未初始化（loadOptions 補成全部）。 */
  const pool = useLocalStorage<string[]>('aiq.verticalFilter.pool', []);
  /** 工具列實際篩選選中（限 pool 內）；空＝尚未初始化（loadOptions 補成全 pool）。 */
  const filter = useLocalStorage<string[]>('aiq.verticalFilter.filter', []);

  /** 工具列可選分類＝選項池（並限制在 allOptions 內，防 config 移除分組後殘留）。 */
  const toolbarOptions = computed(() =>
    allOptions.value.length ? pool.value.filter((g) => allOptions.value.includes(g)) : [...pool.value],
  );

  /**
   * 生效篩選（供各查詢統一讀取）：**嚴格限制**——即使全選也送分組名，後端展開成 CATEGORY 代碼做
   * IN 過濾，使數據範圍永遠鎖在選項池涵蓋的分類內（不放行未歸入任何分組的其他分類）。
   * 僅選項池尚未初始化（opts 為空）時回空陣列＝不篩選，避免載入前把資料濾空。
   * 設定頁改選項池本身不直接篩資料，只變更可選範圍（隨後 filter 修剪 ⊆ pool 再生效）。
   */
  const activeGroups = computed<string[]>(() => {
    const opts = toolbarOptions.value;
    if (!opts.length) return []; // 未初始化＝不篩選（兜底，避免濾空）
    const sel = filter.value.filter((g) => opts.includes(g));
    return sel.length ? [...sel] : [];
  });

  /** 載入全部分組並初始化 pool / filter（首次皆預設全選）；失敗吞例外回空。 */
  const loadOptions = async () => {
    try {
      const r = await getProductVerticalResolved();
      allOptions.value = Object.keys(r.groups || {});
      if (!pool.value.length) pool.value = [...allOptions.value];
      if (!filter.value.length) filter.value = [...pool.value];
    } catch {
      allOptions.value = [];
    }
  };

  /**
   * 設定選項池（規則配置頁，複選）：至少保留 1 個；同步修剪 filter ⊆ pool（被移除項不殘留，
   * 修剪後為空則補成全 pool），確保工具列篩選永遠落在可選範圍內。
   * @param next 新選項池分組名清單
   */
  const setPool = (next: string[]) => {
    if (!next.length) return; // 至少保留 1 個
    pool.value = [...next];
    const trimmed = filter.value.filter((g) => next.includes(g));
    filter.value = trimmed.length ? trimmed : [...next];
  };

  /**
   * 設定工具列篩選（歸因列表，複選）：至少保留 1 個（剩 1 不可移除）；限制在 pool 內。
   * @param next 新篩選選中分組名清單
   */
  const setFilter = (next: string[]) => {
    const cleaned = next.filter((g) => pool.value.includes(g));
    if (!cleaned.length) return; // 剩 1 不可移除
    filter.value = [...cleaned];
  };

  return { allOptions, pool, filter, toolbarOptions, activeGroups, loadOptions, setPool, setFilter };
});

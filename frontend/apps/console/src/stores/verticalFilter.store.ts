// 全局商品垂直分類篩選（跨頁共享，狀態持久化跨 session）：
// ① 顯示順序＝直接採用後端 bd_tag_vertical 規則 verticals 選項池的陣列順序（get_verticals_resolved()
//    保序回傳）；順序調整改在「商品垂直分類」設定頁的 Vertical 選項池編輯器拖曳，本 store 不再自管
//    本地順序（2026-07-27 前舊機制：規則配置頁另開一份拖曳排序清單＋本地 order 持久化，已退役）。
// ② 篩選 filter（歸因列表工具列選中）＝實際套用到 列表 / 縱覽 / 未判 / 初判 scope 的篩選；
//    **預設空＝不篩選**（不像舊版自動全選），使用者需主動勾選才會收斂資料範圍。
import { computed, ref } from 'vue';
import { defineStore } from 'pinia';
import { useLocalStorage } from '@vueuse/core';
import { getVerticalResolved } from '@/api';

export const useVerticalFilterStore = defineStore('verticalFilter', () => {
  /** 全部 Vertical，依 bd_tag_vertical 選項池陣列順序（不持久化，每 session 由 loadOptions 補齊）。 */
  const allOptions = ref<string[]>([]);
  /** 工具列實際篩選選中（複選）；預設空＝不篩選。 */
  const filter = useLocalStorage<string[]>('aiq.verticalFilter.filter', []);

  /** 工具列可選 Vertical＝直接沿用後端選項池順序（順序由「商品垂直分類」設定頁拖曳維護）。 */
  const toolbarOptions = computed(() => allOptions.value);

  /** 生效篩選（供各查詢統一讀取）：篩選為空＝不篩選（後端不收窄，回全部來源資料）。 */
  const activeGroups = computed<string[]>(() =>
    filter.value.filter((v) => toolbarOptions.value.includes(v)),
  );

  /** 載入全部 Vertical（可重複呼叫：商品垂直分類存檔後由設定面板主動重呼，使已掛載消費端
   *  即時反映新增/刪除/重排的 Vertical）；失敗吞例外回空。不動 filter——純顯示用資料源更新。 */
  const loadOptions = async () => {
    try {
      const r = await getVerticalResolved();
      allOptions.value = [...(r.verticals ?? [])];
    } catch {
      allOptions.value = [];
    }
  };

  /**
   * 設定工具列篩選（歸因列表，複選）：可清空（＝不篩選，非舊版「剩 1 不可移除」限制）。
   * @param next 新篩選選中 Vertical 名稱清單
   */
  const setFilter = (next: string[]) => {
    filter.value = next.filter((v) => toolbarOptions.value.includes(v));
  };

  return {
    allOptions,
    filter,
    toolbarOptions,
    activeGroups,
    loadOptions,
    setFilter,
  };
});

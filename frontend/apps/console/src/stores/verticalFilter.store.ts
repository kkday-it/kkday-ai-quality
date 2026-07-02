// 全局商品垂直分類篩選：跨頁共享的開關 + 選中分類，統一控制整個 AI 法官（歸因列表 total /
// 全部未判 / 歸因縱覽 dashboard / 初判 scope）。狀態持久化（useLocalStorage）跨 session 存活。
import { computed, ref } from 'vue';
import { defineStore } from 'pinia';
import { useLocalStorage } from '@vueuse/core';
import { getProductVerticalResolved } from '@/api';

export const useVerticalFilterStore = defineStore('verticalFilter', () => {
  /** 開關：是否啟用全局垂直分類篩選（關閉時所有查詢不受其約束）。 */
  const enabled = useLocalStorage('aiq.verticalFilter.enabled', false);
  /** 選中的分類分組名（Exp/Tix/Tour/Charter…）。 */
  const groups = useLocalStorage<string[]>('aiq.verticalFilter.groups', []);
  /** 可選分組（來自 config/product_vertical 動態解析）。 */
  const options = ref<string[]>([]);

  /** 生效分組：關閉或未選時回空陣列（＝不篩選）；供各查詢統一讀取。 */
  const activeGroups = computed(() =>
    enabled.value && groups.value.length ? [...groups.value] : [],
  );

  /** 載入可選分組（getProductVerticalResolved 的 groups keys）；失敗吞例外回空。 */
  const loadOptions = async () => {
    try {
      const r = await getProductVerticalResolved();
      options.value = Object.keys(r.groups || {});
    } catch {
      options.value = [];
    }
  };

  return { enabled, groups, options, activeGroups, loadOptions };
});

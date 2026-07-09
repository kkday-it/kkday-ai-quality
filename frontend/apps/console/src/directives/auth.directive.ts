// v-auth 指令：v-auth="'permission.key'" 無該權限則移除元素（DOM 層 gating，非僅隱藏）。
import type { Directive } from 'vue';
import { usePermissionStore } from '@/stores';

/**
 * v-auth 權限指令：綁定值為 business-key（string）。無權限時直接從 DOM 移除元素
 * （比照 be2 v-auth 語意：移除而非 display:none，避免殘留可被觸發的入口）。
 * mounted 時判定一次——權限清單於登入 / boot 已載入（見 permission.store）；權限變動需重進頁面。
 */
export const vAuth: Directive<HTMLElement, string> = {
  mounted(el, binding) {
    const key = binding.value;
    if (key && !usePermissionStore().hasPermission(key)) {
      el.remove();
    }
  },
};

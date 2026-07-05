// 📊 質檢概覽路由：判決系統 KPI 總覽（單頁，接真 attribution_overview）。
// 原「三業務目標」子路由（content/presale/postsale）為 mock 敘事，已改為單一真實 KPI 總覽。
// lazy import：DashboardView 分獨立 chunk（含 echarts），進入概覽才載入。
import type { RouteRecordRaw } from 'vue-router';

export const overviewRoutes: RouteRecordRaw = {
  path: '/overview',
  component: () => import('../pages/DashboardView.vue'),
  meta: { text: '判決系統總覽' },
};

/**
 * 概覽視圖 tab：單頁總覽，無次級 tab（殼層 modules.ts 據此於 topbar 下不渲 tab 列）。
 * 保留 export 契約供 modules.ts；未來若再分檢視於此擴充。
 */
export const OVERVIEW_TABS: ReadonlyArray<{ key: string; label: string }> = [];

// 💰 AI 消耗模組路由：單頁 dashboard（lazy import，進入才載入 echarts chunk）。
import type { RouteRecordRaw } from 'vue-router';

export const usageRoutes: RouteRecordRaw = {
  path: '/usage',
  component: () => import('../pages/UsageOverview.vue'),
  meta: { text: 'AI 消耗' },
};

/** 單頁模組無次級 tab（殼層 modules.ts 據此於 topbar 下不渲 tab 列）。 */
export const USAGE_TABS: ReadonlyArray<{ key: string; label: string }> = [];

// 📊 質檢概覽模組路由：總覽 + 三業務目標（共用 DashboardView，config 驅動）+ 自訂組合。
// 父路由無 component（僅分組 + 重導）；子路由於殼層 <router-view> 渲染，tab 由 children meta.text 衍生。
import type { RouteRecordRaw } from 'vue-router';
import DashboardView from '../pages/DashboardView.vue';

export const overviewRoutes: RouteRecordRaw = {
  path: '/overview',
  redirect: '/overview/content',
  children: [
    { path: 'content', component: DashboardView, meta: { text: '內容質量 & 閉環引擎' } },
    { path: 'presale', component: DashboardView, meta: { text: '售前轉化' } },
    { path: 'postsale', component: DashboardView, meta: { text: '售後履約' } },
  ],
};

/**
 * 概覽視圖 tab（由 children meta.text 衍生，單一真相；與 JUDGE_TABS 同模式）。
 * 殼層 modules.ts 據此於 topbar 下渲 tab 列。
 */
export const OVERVIEW_TABS = (overviewRoutes.children ?? [])
  .filter((c) => c.meta?.text)
  .map((c) => ({ key: `/overview/${String(c.path)}`, label: c.meta!.text as string }));

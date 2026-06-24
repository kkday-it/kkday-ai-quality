// ⚖️ AI 法官模組路由子樹：/judge 為頂級節點，三個視圖為其子路由。
import type { RouteRecordRaw } from 'vue-router';
import JudgeLayout from '../JudgeLayout.vue';
import DataUpload from '../pages/DataUpload.vue';
import Analytics from '../pages/Analytics.vue';
import ProductDetail from '../pages/ProductDetail.vue';

export const judgeRoutes: RouteRecordRaw = {
  path: '/judge',
  component: JudgeLayout,
  redirect: '/judge/upload',
  children: [
    { path: 'upload', component: DataUpload, meta: { text: '資料上傳' } }, // 售前售後進線等多來源·批次管理
    { path: 'analytics', component: Analytics, meta: { text: 'RD／品控 分析' } }, // 出口 B
    { path: 'product', component: ProductDetail, meta: { text: 'PM／AM 單品' } }, // 出口 A
  ],
};

/**
 * 頂部視圖 tab（由上方 children 的 meta.text 衍生，單一真相）。
 * 菜單與路由不再雙源：新增 / 改名 tab 只需動 children 一處。
 */
export const JUDGE_TABS = (judgeRoutes.children ?? [])
  .filter((c) => c.meta?.text)
  .map((c) => ({ key: `/judge/${String(c.path)}`, label: c.meta!.text as string }));

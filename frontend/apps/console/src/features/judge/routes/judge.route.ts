// ⚖️ AI 法官模組路由子樹：/judge 為頂級節點，三個視圖為其子路由。
import type { RouteRecordRaw } from 'vue-router';
import JudgeLayout from '../JudgeLayout.vue';
import Analytics from '../pages/Analytics.vue';
import AttributionList from '../pages/AttributionList.vue';
import AttributionOverview from '../pages/AttributionOverview.vue';
import DataUpload from '../pages/DataUpload.vue';
import ProductDetail from '../pages/ProductDetail.vue';
import RuleManager from '../pages/RuleManager.vue';

export const judgeRoutes: RouteRecordRaw = {
  path: '/judge',
  component: JudgeLayout,
  redirect: '/judge/rules',
  children: [
    { path: 'rules', component: RuleManager, meta: { text: '判決規則' } }, // config/ai_judge 7 域判決規則：面板/JSON 雙編 + schema + 歷史對比恢復 + PG 版本化（置首·預設視圖）
    { path: 'upload', component: DataUpload, meta: { text: '資料上傳' } }, // 售前售後進線等多來源·批次管理
    { path: 'list', component: AttributionList, meta: { text: '歸因列表' } }, // 初判歸因：選來源+模型+數量 → L1~L3 列表 + 導出
    { path: 'attribution', component: AttributionOverview, meta: { text: '歸因縱覽' } }, // 歸因列表的聚合儀表板：KPI + 漏斗 + L1~L3 + 趨勢
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

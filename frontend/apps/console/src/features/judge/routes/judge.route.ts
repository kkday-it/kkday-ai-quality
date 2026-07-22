// ⚖️ AI 法官模組路由子樹：/judge 為頂級節點，三個視圖為其子路由。
// 各頁 lazy import（route-level code splitting）：進入該視圖才載入其 chunk，縮小主 bundle。
import type { RouteRecordRaw } from 'vue-router';

export const judgeRoutes: RouteRecordRaw = {
  path: '/judge',
  component: () => import('../JudgeLayout.vue'),
  redirect: '/judge/rules',
  children: [
    {
      path: 'rules',
      component: () => import('../pages/RuleManager.vue'),
      meta: { text: '規則配置' },
    }, // config/ai_judge 初判規則 + 商品垂直分類：面板/JSON 雙編 + schema + 歷史對比恢復 + PG 版本化（置首·預設視圖）
    {
      path: 'upload',
      component: () => import('../pages/DataUpload.vue'),
      meta: { text: '資料上傳' },
    }, // 售前售後進線等多來源·批次管理
    {
      path: 'list',
      component: () => import('../pages/AttributionList.vue'),
      meta: { text: '歸因列表' },
    }, // 初判歸因：選來源+模型+數量 → L1~L2 列表 + 導出
    {
      path: 'prompt-debug',
      component: () => import('../pages/PromptDebugger.vue'),
      meta: { text: 'Prompt 調試' },
    }, // 任意售後對話：可編 Prompt + 臨時模型旋鈕 + 流式 JSON + schema 校驗 + 單次計費
    {
      path: 'attribution',
      component: () => import('../pages/AttributionOverview.vue'),
      meta: { text: '歸因概覽' },
    }, // 聚合儀表板：縱覽 + 各來源專屬概覽（KPI + 漏斗 + L1~L2 + 趨勢 + PDF 導出）
  ],
};

/**
 * 頂部視圖 tab（由上方 children 的 meta.text 衍生，單一真相）。
 * 菜單與路由不再雙源：新增 / 改名 tab 只需動 children 一處。
 */
export const JUDGE_TABS = (judgeRoutes.children ?? [])
  .filter((c) => c.meta?.text)
  .map((c) => ({ key: `/judge/${String(c.path)}`, label: c.meta!.text as string }));

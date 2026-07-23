// 根路由表：彙集各 feature 的路由子樹，頂級路由（judge / settings）於此並列。
import type { RouteRecordRaw } from 'vue-router';
import { authRoutes } from '@/features/auth/routes';
import { judgeRoutes } from '@/features/judge/routes';
import { overviewRoutes } from '@/features/overview/routes';
import { usageRoutes } from '@/features/usage/routes';

// 註：設定（LLM/QC 連線、商品垂直分類、資料導入、導出偏好）改以殼層右滑抽屜的五分頁呈現
// （layouts/components/SettingsDrawer.vue），分頁狀態同步 URL query(?settings=llm|qc|vertical|import|export)，
// 不再佔用獨立 /settings 路由；無獨立「帳號」路由或抽屜（去帳戶系統後已整個退役）。
// 首頁導向 AI 質檢縱覽（整體鳥瞰），AI 法官為其下一環。
export const routes: RouteRecordRaw[] = [
  { path: '/', redirect: '/overview' },
  authRoutes,
  overviewRoutes,
  judgeRoutes,
  usageRoutes,
];

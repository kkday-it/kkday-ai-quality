// 根路由表：彙集各 feature 的路由子樹，頂級路由（judge / settings）於此並列。
import type { RouteRecordRaw } from 'vue-router';
import { authRoutes } from '@/features/auth/routes';
import { judgeRoutes } from '@/features/judge/routes';
import { overviewRoutes } from '@/features/overview/routes';
import { usageRoutes } from '@/features/usage/routes';

// 註：設定（帳號 / 模型配置）改以殼層右滑抽屜的兩分頁呈現（App.vue），
// 分頁狀態同步 URL query(?settings=account|model)，不再佔用獨立 /settings 路由。
// 首頁導向 AI 質檢縱覽（整體鳥瞰），AI 法官為其下一環。
export const routes: RouteRecordRaw[] = [
  { path: '/', redirect: '/overview' },
  authRoutes,
  overviewRoutes,
  judgeRoutes,
  usageRoutes,
];

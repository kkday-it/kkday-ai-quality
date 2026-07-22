// 🔐 帳號模組路由：be2 SSO 登入入口（本地模式無登入系統，router 守衛會直接擋 /login 導回首頁）。
// public 標記供 router guard 在 be2 模式放行未登入存取。
// lazy import：登入頁分出獨立 chunk，未登入首屏不必載入主控台其餘程式碼。
import type { RouteRecordRaw } from 'vue-router';

export const authRoutes: RouteRecordRaw = {
  path: '/login',
  component: () => import('../pages/Login.vue'),
  meta: { public: true },
};

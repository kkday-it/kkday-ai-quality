// 🔐 帳號模組路由：登入 / 註冊（同頁切換）。public 標記供 router guard 放行未登入存取。
// lazy import：登入頁分出獨立 chunk，未登入首屏不必載入主控台其餘程式碼。
import type { RouteRecordRaw } from 'vue-router';

export const authRoutes: RouteRecordRaw = {
  path: '/login',
  component: () => import('../pages/Login.vue'),
  meta: { public: true },
};

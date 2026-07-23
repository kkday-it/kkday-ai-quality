// Router 組裝：history 模式 + 根路由表（路由定義已拆分至 routes.ts 與各 feature/routes.ts）。
import { createRouter, createWebHistory } from 'vue-router';
import { routes } from './routes';
import { AUTH_PROVIDER, getToken } from '@/api';

const router = createRouter({
  history: createWebHistory(),
  routes,
});

// 守衛：① 本地模式無登入系統，恆放行（不判斷 token）；be2 模式未登入只能進 public（/login）
// ② be2 已登入訪 /login 導回首頁（頁面級權限判斷走 usePermission() composable，見 composables/usePermission.ts）。
router.beforeEach((to) => {
  const authed = AUTH_PROVIDER !== 'be2' || !!getToken();
  if (!authed && !to.meta.public) return { path: '/login' };
  if (authed && AUTH_PROVIDER === 'be2' && to.path === '/login') return { path: '/' };
  if (AUTH_PROVIDER !== 'be2' && to.path === '/login') return { path: '/' }; // 本地模式無登入頁可看
});

export default router;

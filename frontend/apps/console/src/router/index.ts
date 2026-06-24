// Router 組裝：history 模式 + 根路由表（路由定義已拆分至 routes.ts 與各 feature/routes.ts）。
import { createRouter, createWebHistory } from 'vue-router';
import { routes } from './routes';
import { getToken } from '@/api';

const router = createRouter({
  history: createWebHistory(),
  routes,
});

// 認證守衛：未登入只能進 public 路由（/login）；已登入訪 /login 導回首頁。
router.beforeEach((to) => {
  const authed = !!getToken();
  if (!authed && !to.meta.public) return { path: '/login' };
  if (authed && to.path === '/login') return { path: '/' };
});

export default router;

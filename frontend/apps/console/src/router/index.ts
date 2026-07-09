// Router 組裝：history 模式 + 根路由表（路由定義已拆分至 routes.ts 與各 feature/routes.ts）。
import { createRouter, createWebHistory } from 'vue-router';
import { routes } from './routes';
import { getToken } from '@/api';
import { usePermissionStore } from '@/stores';

const router = createRouter({
  history: createWebHistory(),
  routes,
});

// 守衛：① 未登入只能進 public（/login）② 已登入訪 /login 導回首頁
// ③ 路由宣告 meta.permissions 時，缺任一權限導回首頁（權限由可替換框架 store 提供，初值讀 localStorage 快取）。
router.beforeEach((to) => {
  const authed = !!getToken();
  if (!authed && !to.meta.public) return { path: '/login' };
  if (authed && to.path === '/login') return { path: '/' };
  const required = to.meta.permissions;
  if (authed && required?.length) {
    const perm = usePermissionStore();
    if (!required.every((k) => perm.hasPermission(k))) return { path: '/' };
  }
});

export default router;

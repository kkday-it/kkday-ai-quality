// 🔐 帳號模組路由：登入 / 註冊（同頁切換）。public 標記供 router guard 放行未登入存取。
import type { RouteRecordRaw } from 'vue-router';
import Login from '../pages/Login.vue';

export const authRoutes: RouteRecordRaw = {
  path: '/login',
  component: Login,
  meta: { public: true },
};

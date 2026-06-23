import { createRouter, createWebHashHistory } from 'vue-router';

import Analytics from '../pages/Analytics.vue';
import ProductDetail from '../pages/ProductDetail.vue';

export default createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', redirect: '/analytics' },
    { path: '/analytics', component: Analytics }, // 出口B：RD/品控 分析
    { path: '/product', component: ProductDetail }, // 出口A：PM/AM 單品診斷
  ],
});

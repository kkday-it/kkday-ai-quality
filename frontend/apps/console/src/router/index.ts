import { createRouter, createWebHistory } from 'vue-router';

import DataUpload from '../pages/DataUpload.vue';
import Analytics from '../pages/Analytics.vue';
import ProductDetail from '../pages/ProductDetail.vue';
import Settings from '../pages/Settings.vue';

export default createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/upload' },
    { path: '/upload', component: DataUpload }, // 資料上傳（售前售後進線等多來源·批次管理）
    { path: '/analytics', component: Analytics }, // 出口B：RD/品控 分析
    { path: '/product', component: ProductDetail }, // 出口A：PM/AM 單品診斷
    { path: '/settings', component: Settings }, // ⚙️ LLM 模型配置
  ],
});

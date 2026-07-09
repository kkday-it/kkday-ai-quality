import ArcoVue from '@arco-design/web-vue';
import '@arco-design/web-vue/dist/arco.css';
import { createPinia } from 'pinia';
import { createApp } from 'vue';
import './style.css';

import App from './App.vue';
import router from './router';
import { vAuth } from './directives/auth.directive';
import { i18n, setupI18n } from './i18n';

// mount 前先載入語系訊息（loader 為唯一替換接縫；現靜態 glob 同步解析，故 .then 幾近零延遲）。
// 用 .then 而非 top-level await——build target(es2020) 不支援 top-level await。
setupI18n().then(() => {
  createApp(App)
    .use(createPinia())
    .use(ArcoVue)
    .use(router)
    .use(i18n)
    .directive('auth', vAuth)
    .mount('#app');
});

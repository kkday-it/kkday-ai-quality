import ArcoVue from '@arco-design/web-vue';
import '@arco-design/web-vue/dist/arco.css';
import { createPinia } from 'pinia';
import { createApp } from 'vue';
import './style.css';

import App from './App.vue';
import router from './router';
import { vAuth } from './directives/auth.directive';

createApp(App)
  .use(createPinia())
  .use(ArcoVue)
  .use(router)
  .directive('auth', vAuth)
  .mount('#app');

import { fileURLToPath, URL } from 'node:url';
import { defineConfig, searchForWorkspaceRoot } from 'vite';
import vue from '@vitejs/plugin-vue';

// repo 根的跨語言共用預設值目錄（config/defaults.json）；前端與 Python 後端同讀此檔。
const configDir = fileURLToPath(new URL('../../../config', import.meta.url));

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      // `@` → src，跨 feature import 用絕對路徑，避免深層 ../../ 噪音
      '@': fileURLToPath(new URL('./src', import.meta.url)),
      // `@config` → repo 根 config/，供 import defaults.json 單一真相源
      '@config': configDir,
    },
  },
  build: {
    // echarts / arco / vue 全家桶拆獨立 vendor chunk：縮小主 chunk、利於瀏覽器快取（vendor 不常變）
    chunkSizeWarningLimit: 900,
    rollupOptions: {
      output: {
        manualChunks: {
          echarts: ['echarts', 'vue-echarts'],
          arco: ['@arco-design/web-vue'],
          vue: ['vue', 'vue-router', 'pinia'],
        },
      },
    },
  },
  server: {
    // 可 env 覆蓋（VITE_DEV_PORT），預設 5273；對齊後端 CORS_ALLOW_ORIGINS 預設
    port: Number(process.env.VITE_DEV_PORT) || 5273,
    strictPort: true,
    // config/ 在 pnpm workspace（frontend/）之外，dev server 預設拒讀；顯式放行該目錄。
    fs: {
      allow: [searchForWorkspaceRoot(process.cwd()), configDir],
    },
    proxy: {
      // 開發時前端 /api → 後端 FastAPI；可 env 覆蓋（VITE_BACKEND_URL），預設 8100
      '/api': process.env.VITE_BACKEND_URL || 'http://localhost:8100',
    },
  },
});

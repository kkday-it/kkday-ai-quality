import { fileURLToPath, URL } from 'node:url';
import { searchForWorkspaceRoot } from 'vite';
// defineConfig 取自 vitest/config（vite defineConfig 的超集，額外提供 `test` 欄位型別）。
import { defineConfig } from 'vitest/config';
import vue from '@vitejs/plugin-vue';

// repo 根的跨語言共用目錄；前端與 Python 後端同讀。
// config/＝業務可調配置；constants/＝固定共用參照常數（enum / 代碼字典），按維度分子資料夾。
const configDir = fileURLToPath(new URL('../../../config', import.meta.url));
const constantsDir = fileURLToPath(new URL('../../../constants', import.meta.url));

export default defineConfig({
  plugins: [vue()],
  // 純函式 util 單元測試（無 DOM）→ node 環境即可；`.test.ts` 就近置於 utils/ 旁，沿用上方 resolve.alias。
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts'],
  },
  resolve: {
    alias: {
      // `@` → src，跨 feature import 用絕對路徑，避免深層 ../../ 噪音
      '@': fileURLToPath(new URL('./src', import.meta.url)),
      // `@config` → repo 根 config/，供 import defaults.json 單一真相源
      '@config': configDir,
      // `@constants` → repo 根 constants/，固定共用參照常數（前後端同源）
      '@constants': constantsDir,
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
    // config/ 與 constants/ 在 pnpm workspace（frontend/）之外，dev server 預設拒讀；顯式放行。
    fs: {
      allow: [searchForWorkspaceRoot(process.cwd()), configDir, constantsDir],
    },
    proxy: {
      // 開發時前端 /api → 後端 FastAPI；可 env 覆蓋（VITE_BACKEND_URL），預設 8100
      '/api': process.env.VITE_BACKEND_URL || 'http://localhost:8100',
    },
  },
});

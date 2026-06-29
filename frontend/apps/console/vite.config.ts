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
  server: {
    port: 5273,
    strictPort: true,
    // config/ 在 pnpm workspace（frontend/）之外，dev server 預設拒讀；顯式放行該目錄。
    fs: {
      allow: [searchForWorkspaceRoot(process.cwd()), configDir],
    },
    proxy: {
      // 開發時前端 /api → 後端 FastAPI（8100）
      '/api': 'http://localhost:8100',
    },
  },
});

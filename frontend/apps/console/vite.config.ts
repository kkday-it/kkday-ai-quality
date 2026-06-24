import { fileURLToPath, URL } from 'node:url';
import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      // `@` → src，跨 feature import 用絕對路徑，避免深層 ../../ 噪音
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5273,
    strictPort: true,
    proxy: {
      // 開發時前端 /api → 後端 FastAPI（8100）
      '/api': 'http://localhost:8100',
    },
  },
});

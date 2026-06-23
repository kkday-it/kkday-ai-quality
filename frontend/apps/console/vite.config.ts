import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5273,
    strictPort: true,
    proxy: {
      // 開發時前端 /api → 後端 FastAPI（8100）
      '/api': 'http://localhost:8100',
    },
  },
});

/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{vue,ts}'],
  // 關鍵：關掉 preflight，避免 Tailwind reset 破壞 Arco 元件樣式
  corePlugins: { preflight: false },
  theme: { extend: {} },
  plugins: [],
};

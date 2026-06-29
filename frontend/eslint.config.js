// ESLint flat config（ready-made 預設組合，零調校）。
// 組合：JS 官方推薦 + TypeScript 推薦 + Vue3 推薦 + Prettier 關閉格式衝突。
// 職責分工：格式 → Prettier（見 .prettierrc.json）；品質/bug → 此處 ESLint；型別 → vue-tsc。
import js from '@eslint/js';
import tseslint from 'typescript-eslint';
import pluginVue from 'eslint-plugin-vue';
import prettier from 'eslint-config-prettier';
import globals from 'globals';

export default tseslint.config(
  { ignores: ['**/dist/**', '**/node_modules/**', '**/*.d.ts'] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  ...pluginVue.configs['flat/recommended'],
  {
    // 瀏覽器環境全域（window / document / localStorage…），避免 no-undef 誤報
    languageOptions: { globals: { ...globals.browser } },
  },
  {
    // .vue 的 <script> 用 TS parser
    files: ['**/*.vue'],
    languageOptions: { parserOptions: { parser: tseslint.parser } },
  },
  {
    rules: {
      // 專案既有風格放行（避免大量無意義報錯；要更嚴格再逐條開）
      '@typescript-eslint/no-explicit-any': 'off', // FindingRow 等漸進 typed-refactor 前暫容忍（見 CODE-REVIEW C/E）
      '@typescript-eslint/no-unused-vars': ['warn', { argsIgnorePattern: '^_' }],
      'vue/multi-word-component-names': 'off', // 允許 Settings.vue / Analytics.vue 單字命名
    },
  },
  prettier, // 必須放最後：關掉所有與 Prettier 衝突的格式規則
);

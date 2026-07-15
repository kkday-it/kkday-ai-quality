<script setup lang="ts">
/**
 * 唯讀語法高亮程式碼區塊（Shiki，MIT，與 VS Code 同 TextMate grammar engine）：供未來 SQL / YAML /
 * Python 等程式碼片段展示共用，取代手刻 `<pre>` 純文字。
 *
 * 設計比照專案既有重量套件封裝慣例（Terminal.vue／JsonEditor.vue）：
 * - highlighter 實例為模組級單例（Shiki 官方建議長駐重用，見官方文件 Performance 章節），
 *   跨元件實例共用、僅首次呼叫觸發動態 import（不進初始 bundle）。
 * - 語言/主題採 fine-grained 動態載入（`loadLanguage`），非一次載入整包 web bundle（3.8MB），
 *   用多少載多少。
 * - 本元件只吃「初始 code/lang」，不做 prop→內容雙向同步；外部變更請改 `:key` 重掛
 *   （同 JsonEditor.vue 慣例，避免不必要的重繪邏輯）。
 * - 未知/不支援的 lang 或載入失敗 → 降級純文字顯示，絕不因語言字串打錯而炸頁面。
 */
import { onMounted, ref } from 'vue';
import type { HighlighterGeneric } from 'shiki';

const props = withDefaults(
  defineProps<{
    code: string;
    /** Shiki 語言 id（如 sql/yaml/python/json）；未知/不支援自動降級純文字。 */
    lang?: string;
    /** Shiki 內建主題 id；預設對齊 Arco 亮色介面。 */
    theme?: string;
  }>(),
  { lang: 'text', theme: 'github-light' },
);

const html = ref('');
const ready = ref(false);

type Highlighter = HighlighterGeneric<any, any>;
let highlighterPromise: Promise<Highlighter> | null = null;

/** 取（或建）模組級單例 highlighter；僅首次呼叫觸發 shiki 動態 import + 建立實例。 */
function getHighlighter(): Promise<Highlighter> {
  if (!highlighterPromise) {
    highlighterPromise = import('shiki').then(({ createHighlighter }) =>
      createHighlighter({ themes: [props.theme, 'github-light'], langs: ['text'] }),
    );
  }
  return highlighterPromise;
}

onMounted(async () => {
  try {
    const hl = await getHighlighter();
    if (!hl.getLoadedLanguages().includes(props.lang)) {
      await hl.loadLanguage(props.lang as any); // 未知語言 id 會拋錯，交下方 catch 降級
    }
    if (!hl.getLoadedThemes().includes(props.theme)) {
      await hl.loadTheme(props.theme as any);
    }
    html.value = hl.codeToHtml(props.code, { lang: props.lang, theme: props.theme });
    ready.value = true;
  } catch {
    ready.value = false; // 降級：模板顯示純文字 <pre>，不因語言/主題不支援而空白或報錯
  }
});
</script>

<template>
  <div class="code-block">
    <!-- 安全：html 為 Shiki codeToHtml() 輸出，原始碼內容已由 Shiki 逐字元跳脫為 HTML entity，
         僅外層包 <span> 上色 token，非使用者可控原始 HTML（語法高亮套件唯一正確整合方式）。 -->
    <!-- eslint-disable-next-line vue/no-v-html -->
    <div v-if="ready" v-html="html" />
    <pre v-else class="code-block-plain">{{ code }}</pre>
  </div>
</template>

<style scoped>
.code-block {
  max-height: 480px;
  overflow: auto;
  border-radius: 6px;
  font-size: 12px;
  line-height: 1.6;
}
.code-block :deep(pre) {
  margin: 0;
  padding: 8px;
  overflow: visible; /* 外層 .code-block 統一捲動，避免雙層捲軸 */
}
.code-block-plain {
  margin: 0;
  padding: 8px;
  white-space: pre-wrap;
  word-break: break-all;
  background: #f7f8fa;
}
</style>

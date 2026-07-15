<script setup lang="ts">
/**
 * 初判 Prompt md 編輯器（Prompt-as-Source）：編輯 prompt content 的 text 欄（md 全文），
 * 左寫右即時渲染。prompt content 形態＝{_meta, text}；本元件只改 text，_meta 原樣帶回。
 * 由 RuleManager 以 defineAsyncComponent 懶載入（md-editor-v3 較重，不壓首屏 bundle）。
 * 合法性＝text 為非空字串（結構恆合法；md 三節契約 + drift 由後端 prompt_source.validate 為權威閘）。
 */
import { ref, watch } from 'vue';
import { MdEditor } from 'md-editor-v3';
import 'md-editor-v3/lib/style.css';

const props = defineProps<{
  /** 當前 prompt content（{_meta, text}）。 */
  content: Record<string, unknown>;
}>();

const emit = defineEmits<{
  /** 內容變更 → 回報包好的完整 content + 合法性（比照 JsonEditor/RuleTreePanel 契約）。 */
  (e: 'change', payload: { json: unknown; valid: boolean }): void;
}>();

/** md 全文（編輯態）；初始自 content.text。 */
const md = ref<string>(typeof props.content.text === 'string' ? props.content.text : '');

// 外部 content 換版（切規則 / 存檔後重載 / 恢復版本）→ 同步編輯區。editorKey 重掛已保證多數情境重置，
// 此 watch 兜住同 key 下 content 物件替換（如恢復默認後 selectRule 回填）的邊界。
watch(
  () => props.content,
  (c) => {
    const t = typeof c.text === 'string' ? c.text : '';
    if (t !== md.value) md.value = t;
  },
);

watch(md, (t) => {
  emit('change', { json: { ...props.content, text: t }, valid: t.trim().length > 0 });
});
</script>

<template>
  <!-- flex 撐滿：MdEditor flex-1 min-h-0 由 flex 演算法直接給高（不靠 height:100% 級聯），內部自捲 -->
  <div class="flex h-full min-h-0 flex-col gap-2 overflow-hidden">
    <div class="flex min-h-0 flex-1 flex-col overflow-hidden rounded-lg border">
      <MdEditor
        v-model="md"
        class="min-h-0 flex-1"
        language="en-US"
        :preview="true"
        :code-foldable="false"
        :toolbars-exclude="[
          'github',
          'save',
          'pageFullscreen',
          'fullscreen',
          'htmlPreview',
          'catalog',
        ]"
        :footers="['markdownTotal', 'scrollSwitch']"
      />
    </div>
  </div>
</template>

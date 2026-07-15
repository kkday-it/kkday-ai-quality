<script setup lang="ts">
/**
 * 執行日誌內 JSON 值的唯讀展示：包 `JsonEditor`（vanilla-jsoneditor tree 模式，專案既有共用元件，
 * 見 rule 版本對比 / prompt 對比同套用法），取代純文字 `JSON.stringify` 字串——樹狀摺疊 + 語法
 * 高亮，滿足「一眼看到全部數據」。
 *
 * 掛載後自動展開全部節點：vanilla-jsoneditor 內部動態 import 非同步完成，`expand()` 呼叫時
 * editor 可能尚未就緒（呼叫即靜默略過、不報錯，見 JsonEditor.vue 自身設計），故有界重試數次
 * （每次呼叫皆無害，命中即展開，避免使用者還要手動點開）。
 */
import { onMounted, ref } from 'vue';
import { JsonEditor } from '@/components';

defineProps<{ json: unknown }>();

const editorRef = ref<InstanceType<typeof JsonEditor>>();

onMounted(() => {
  let tries = 0;
  const tryExpand = () => {
    editorRef.value?.expand([], () => true);
    if (++tries < 10) setTimeout(tryExpand, 50);
  };
  tryExpand();
});
</script>

<template>
  <div class="log-json-block mt-1">
    <JsonEditor ref="editorRef" :json="json" read-only mode="tree" />
  </div>
</template>

<style scoped>
/* 覆寫 JsonEditor 預設 60vh（整頁編輯用高度）：日誌內為緊湊小型 JSON 片段，
   固定較矮高度＋內部自行捲動（overflow:hidden 交還 editor 內部 .jse-contents 捲軸，同上游設計）。 */
.log-json-block :deep(.json-editor-host) {
  height: 240px;
}
</style>

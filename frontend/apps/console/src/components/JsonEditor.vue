<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount, shallowRef } from 'vue';
import type { Content, Validator } from 'vanilla-jsoneditor';
// 預設（亮色）主題已內建於套件 JS，無需另引 CSS；themes/ 僅提供 dark 覆蓋（jse-theme-dark.css）。

/**
 * vanilla-jsoneditor（josdejong，MIT）的薄 Vue 包裝：tree / text 雙模式 + 唯讀 + 變更回報。
 *
 * 設計：editor 自管內部狀態，故本元件只吃「初始 json」+ 回報變更，不做 prop→editor 雙向同步
 *   （避免游標跳動 / 更新迴圈）。消費端切換檔案時以 `:key` 重掛即可重置內容。
 * onChange 嘗試把當前內容正規化為 JSON：合法 → 回 { json, valid:true }；
 *   text 模式打到一半語法錯 → { json:undefined, valid:false, error }，供存檔閘判斷。
 * 效能：vanilla-jsoneditor 為巨型套件（內含 CodeMirror），改為掛載時「動態 import」，
 *   使其不進初始 / 頁面 chunk，只在本編輯器實際掛載時才載入（規則頁、config 編輯共用受益）。
 */
const props = withDefaults(
  defineProps<{
    /** 初始 JSON 值（物件 / 陣列）。掛載後即由 editor 接管，外部變更請改 key 重掛。 */
    json: unknown;
    readOnly?: boolean;
    /** 預設 tree（結構化編輯，含驗證）；text＝純文字 JSON。 */
    mode?: 'tree' | 'text';
    /** 選填 JSON Schema：提供時編輯器即時標示違反處（best-effort，建立失敗則略過，後端仍為真閘）。 */
    schema?: Record<string, unknown>;
    /** 撐滿父容器高度（height:100%）取代預設 60vh 上限；用於整頁編輯（如判決規則頁）。 */
    fill?: boolean;
  }>(),
  { readOnly: false, mode: 'tree', fill: false },
);

// jsoneditor API 於掛載時動態載入；型別走 import type（編譯期擦除，不進 bundle）
type JseModule = typeof import('vanilla-jsoneditor');
let jse: JseModule | undefined;

/** best-effort 建 schema 驗證器；2020-12 等 ajv 不支援時優雅回 undefined（不阻斷編輯器）。 */
function buildValidator(): Validator | undefined {
  if (!props.schema || !jse) return undefined;
  try {
    return jse.createAjvValidator({ schema: props.schema });
  } catch {
    return undefined; // 後端 422 為最終驗證閘
  }
}

const emit = defineEmits<{
  /** 內容變更：valid 時帶正規化後的 json；invalid 時 json 為 undefined 並附 error。 */
  (e: 'change', payload: { json: unknown; valid: boolean; error?: string }): void;
}>();

const el = ref<HTMLDivElement>();
// editor 實例非響應式（內部自管 DOM/狀態），用 shallowRef 持有以便 unmount 銷毀
const editor = shallowRef<ReturnType<JseModule['createJSONEditor']>>();

/** editor 內容變更 → 嘗試正規化為 JSON 並回報合法性。 */
const onChange = (content: Content): void => {
  if (!jse) return;
  try {
    const { json } = jse.toJSONContent(content);
    emit('change', { json, valid: true });
  } catch (e) {
    emit('change', { json: undefined, valid: false, error: e instanceof Error ? e.message : String(e) });
  }
};

onMounted(async () => {
  if (!el.value) return;
  jse = await import('vanilla-jsoneditor');
  // await 後元件可能已卸載（快速切換）：el 已無 → 放棄建立，交給 onBeforeUnmount
  if (!el.value) return;
  editor.value = jse.createJSONEditor({
    target: el.value,
    props: {
      content: { json: props.json } as Content,
      mode: props.mode === 'text' ? jse.Mode.text : jse.Mode.tree,
      readOnly: props.readOnly,
      validator: buildValidator(),
      onChange,
    },
  });
});

onBeforeUnmount(() => {
  // destroy 回 Promise；unmount 同步流程中觸發即可，無需 await
  void editor.value?.destroy();
  editor.value = undefined;
});
</script>

<template>
  <div ref="el" class="jse-theme-default json-editor-host" :class="{ 'json-editor-fill': fill }" />
</template>

<style scoped>
/* 給編輯器一個合理高度上限 + 內捲，避免大型 config（如 attribution_tree 104KB）撐爆抽屜 */
.json-editor-host {
  --jse-font-family-mono: ui-monospace, SFMono-Regular, Menlo, monospace;
  max-height: 60vh;
  overflow: auto;
}
/* fill：整頁編輯時撐滿父容器（父須有定高），取代 60vh 上限 */
.json-editor-fill {
  max-height: none;
  height: 100%;
}
</style>

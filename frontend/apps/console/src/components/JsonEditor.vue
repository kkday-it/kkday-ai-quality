<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount, shallowRef, watch } from 'vue';
import type {
  Content,
  JSONEditorPropsOptional,
  OnClassName,
  OnExpand,
  Validator,
} from 'vanilla-jsoneditor';

/** vanilla-jsoneditor 的節點路徑（各段字串；套件未公開匯出 JSONPath 型別，故本地別名）。 */
type JsonPath = string[];
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
    /** 撐滿父容器高度（height:100%）取代預設 60vh 上限；用於整頁編輯（如初判規則頁）。 */
    fill?: boolean;
    /** 高度隨內容自然撐開（取代固定 60vh + 內部捲動）：tree 模式逐節點皆為實體 DOM（非虛擬滾動），
     * 撐開安全；用於外層已提供整體捲動容器、不want 巢狀雙捲軸的情境（如日誌執行流全展開）。 */
    autoHeight?: boolean;
    /** 選填節點 class 回呼：依 path/value 回傳 class 名（如版本對比標紅）；變更即時套用（read-only 情境）。 */
    onClassName?: OnClassName;
  }>(),
  {
    readOnly: false,
    mode: 'tree',
    fill: false,
    autoHeight: false,
    schema: undefined,
    onClassName: undefined,
  },
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
    emit('change', {
      json: undefined,
      valid: false,
      error: e instanceof Error ? e.message : String(e),
    });
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
      onClassName: props.onClassName,
      onChange,
    },
  });
});

// read-only（歷史 / 版本對比）情境：外部 json 或 onClassName 於掛載後才就緒（非同步載入 / 切版）時推入 editor。
// 修「掛載時內容尚未載入 → 空白，需重選版本才顯示」的競態；同時讓 diff 標紅隨版本即時更新。
// 可編輯情境不走此路（維持 :key 重掛），避免打字→回寫 json→updateProps 重置游標的迴圈。
watch(
  () => [props.json, props.onClassName] as const,
  ([json, onClassName]) => {
    if (!props.readOnly || !editor.value) return;
    editor.value.updateProps({
      content: { json } as Content,
      onClassName,
    } as JSONEditorPropsOptional);
  },
);

/**
 * 暴露命令式方法給消費端（版本對比用）：
 * - expand(path, cb)：自 path 起逐節點依 cb 決定是否展開（tree 模式）
 * - scrollTo(path)：捲動並聚焦指定節點
 * - refresh()：重繪
 * 皆對 editor 尚未就緒 / 非 tree 模式做保護，靜默略過不拋錯。
 */
defineExpose({
  expand: (path: JsonPath, callback?: OnExpand): void => {
    try {
      editor.value?.expand(path, callback);
    } catch {
      /* 非 tree 模式 / editor 未就緒 → 略過 */
    }
  },
  // 目標 path 可能只存在於某一版（增 / 刪）→ 另一欄 scrollTo 會 reject，靜默吞掉不干擾對齊
  scrollTo: (path: JsonPath): Promise<void> =>
    (editor.value?.scrollTo(path) ?? Promise.resolve()).catch(() => {}),
  refresh: (): Promise<void> => editor.value?.refresh() ?? Promise.resolve(),
});

onBeforeUnmount(() => {
  // destroy 回 Promise；unmount 同步流程中觸發即可，無需 await
  void editor.value?.destroy();
  editor.value = undefined;
});
</script>

<template>
  <div
    ref="el"
    class="jse-theme-default json-editor-host"
    :class="{ 'json-editor-fill': fill, 'json-editor-auto': autoHeight }"
  />
</template>

<style scoped>
/* 定高 + 外層不捲：讓 vanilla-jsoneditor 內部 menu（text/tree/table 工具列）與導覽列固定，只捲 .jse-contents。
   原 max-height + overflow:auto 會把整塊（含工具列）一起捲走；改給定高、overflow 交還 editor 內部 flex 佈局。 */
.json-editor-host {
  --jse-font-family-mono: ui-monospace, SFMono-Regular, Menlo, monospace;
  height: 60vh;
  overflow: hidden;
}
/* fill：整頁編輯時撐滿父容器（父須有定高），取代 60vh；仍由 editor 內部捲 contents（工具列固定） */
.json-editor-fill {
  height: 100%;
}
/* autoHeight：不設高度上限、交還捲動給外層容器（呼應「內容區域不再內捲，完整展示」需求）；
   tree 模式節點為實體 DOM 逐一渲染，非虛擬滾動，撐開不影響渲染正確性。 */
.json-editor-auto {
  height: auto;
  overflow: visible;
}
.json-editor-auto :deep(.jse-main) {
  height: auto;
}
.json-editor-auto :deep(.jse-contents) {
  overflow: visible;
}
/* 版本對比：onClassName 標記的變動節點染紅（淡底 + 紅值），用 :deep 穿透 editor 內部 DOM。
   顏色取 Arco 全域 token（arco.css 已於 main.ts 全域載入），亮 / 暗主題一致。 */
.json-editor-host :deep(.jse-diff-changed) {
  background: var(--color-danger-light-1);
}
.json-editor-host :deep(.jse-diff-changed .jse-value) {
  color: rgb(var(--danger-6));
  font-weight: 600;
}
</style>

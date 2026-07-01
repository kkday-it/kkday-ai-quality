<script setup lang="ts">
import { ref, computed, watch, defineAsyncComponent } from 'vue';
import { useElementVisibility } from '@vueuse/core';
import { isEqual, cloneDeep } from 'lodash-es';
import { Message } from '@arco-design/web-vue';
import { StateGuard } from '@/components';
import { getConfigFile, saveConfigFile } from '@/api';

// JsonEditor 內含 vanilla-jsoneditor（codemirror + svelte，體積大）；非同步載入使其切出獨立 chunk，
// 僅在 config 面板真正展開時才下載，避免拖累 app 殼層主 bundle（符合單路由 < 200KB 預算）。
const JsonEditor = defineAsyncComponent(() => import('@/components/JsonEditor.vue'));

/**
 * 單一 config/taxonomy JSON 檔的查看 / 編輯面板：載入 → tree/text 編輯 → 驗證閘 → 存回後端。
 *
 * 三態（loading/error/success）由 StateGuard 統一；存檔僅在「內容合法且有變更」時可按。
 * 後端寫入前驗 JSON + .bak 備份 + taxonomy.reload()，故存檔成功即代表 judge 鏈已生效
 * （reloaded=false 時提示需檢查結構 / 重啟）。
 */
const props = withDefaults(defineProps<{ file: string; readOnly?: boolean }>(), {
  readOnly: false,
});

const loading = ref(true);
const error = ref('');
const saving = ref(false);
/** 後端載入的基準值（存檔成功後更新；用以判斷 dirty）。 */
const baseline = ref<unknown>();
/** 編輯器當前內容（合法時更新）。 */
const edited = ref<unknown>();
const valid = ref(true);
/** 重掛 JsonEditor 用（切回磁碟版時 bump，丟棄未存編輯）。 */
const editorKey = ref(0);

const dirty = computed(() => valid.value && !isEqual(edited.value, baseline.value));
const canSave = computed(() => !props.readOnly && valid.value && dirty.value && !saving.value);

const load = async (): Promise<void> => {
  loading.value = true;
  error.value = '';
  try {
    const r = await getConfigFile(props.file);
    baseline.value = r.content;
    edited.value = cloneDeep(r.content);
    valid.value = true;
    editorKey.value++; // 以最新磁碟內容重掛編輯器
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    loading.value = false;
  }
};

// 懶載入：面板折疊在 accordion 內，僅在「實際展開可見」時才打 API，避免規則 tab 一開就並發數十個請求。
const root = ref<HTMLElement>();
const visible = useElementVisibility(root);
const stopWatch = watch(visible, (v) => {
  if (v) {
    load();
    stopWatch(); // 只首次可見時載入一次；之後重載走「捨棄變更 · 重載」按鈕
  }
});

/** 編輯器內容變更：合法則記錄並開放存檔；非法則鎖存檔。 */
const onChange = (p: { json: unknown; valid: boolean }): void => {
  valid.value = p.valid;
  if (p.valid) edited.value = p.json;
};

const onSave = async (): Promise<void> => {
  if (!canSave.value) return;
  saving.value = true;
  try {
    const r = await saveConfigFile(props.file, edited.value);
    baseline.value = cloneDeep(edited.value);
    if (r.reloaded) Message.success(`已儲存 ${props.file}，judge 規則即時生效`);
    else Message.warning(`已存檔 ${props.file}，但後端重載失敗（請檢查結構或重啟）`);
  } catch (e) {
    Message.error('儲存失敗：' + (e instanceof Error ? e.message : String(e)));
  } finally {
    saving.value = false;
  }
};
</script>

<template>
  <div ref="root">
    <StateGuard :loading="loading" :error="error">
      <div>
      <a-space class="mb-2" wrap>
        <a-tag color="gray" class="font-mono">{{ file }}</a-tag>
        <a-tag v-if="!valid" color="red">JSON 語法錯誤</a-tag>
        <a-tag v-else-if="dirty" color="orange">未儲存變更</a-tag>
        <a-tag v-else color="green">已同步</a-tag>
      </a-space>

      <JsonEditor
        :key="editorKey"
        :json="edited"
        :read-only="readOnly"
        @change="onChange"
      />

      <a-space v-if="!readOnly" class="mt-3">
        <a-button type="primary" :loading="saving" :disabled="!canSave" @click="onSave">
          儲存配置
        </a-button>
        <a-button :disabled="!dirty || saving" @click="load">捨棄變更 · 重載</a-button>
        <span class="text-xs text-[#86909c]">存檔＝寫回 config 檔 + .bak 備份 + 即時 reload</span>
      </a-space>
    </div>
    </StateGuard>
  </div>
</template>

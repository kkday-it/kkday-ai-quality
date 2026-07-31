<script setup lang="ts">
/**
 * 售後根因 Prompt「選一個已存檔版本 → 拿到它的全文」的共用選擇器。
 *
 * 抽出來的理由：這套「依軌分組的下拉 + 選中後按軌別打不同端點取全文」在回歸重跑面板、版本對比
 * 抽屜、跑批抽屜是同一份邏輯的第三次複製，且三處都得各自記得 `isGroup: true` 這個 Arco 契約與
 * `release:` / `draft:` 前綴編碼——漏一個就是靜默壞掉（選項不分組、或前綴切錯打到錯的端點）。
 *
 * 只管「選版本並取全文」，不含「要不要用這一版」的取捨（那是各消費端自己的 radio 語義：回歸面板
 * 要在現行/候選/歷史版之間選，跑批只在頁面當前內容與歷史版之間選）——刻意不把 radio 一起吃進來，
 * 否則會退化成一個靠 boolean prop 切形態的巨型元件。
 */
import { computed, watch } from 'vue';
import { Message } from '@arco-design/web-vue';
import {
  getPromptDraft,
  getPromptRelease,
  type PromptDraftMeta,
  type PromptReleaseMeta,
} from '@/api';

const props = withDefaults(
  defineProps<{
    /** 草稿清單（時間戳版本名，新→舊）。 */
    drafts: PromptDraftMeta[];
    /** 正式版清單（`is_active` 標記使用中那支）。 */
    releases: PromptReleaseMeta[];
    size?: 'mini' | 'small' | 'medium' | 'large';
    placeholder?: string;
  }>(),
  { size: 'small', placeholder: '選擇版本（草稿或正式版）' },
);

/** 選中的版本鍵，格式 `release:<name>` / `draft:<version>`；空＝未選。 */
const versionKey = defineModel<string>({ default: '' });
/** 選中版本的全文（取回後寫入；取失敗會清空，呼叫端據此判斷「拿到內容了沒」）。 */
const text = defineModel<string>('text', { default: '' });
/** 取全文中（下拉自身的 loading 態，呼叫端也可綁去擋送出）。 */
const loading = defineModel<boolean>('loading', { default: false });

/**
 * 依軌分組的下拉選項。
 *
 * `isGroup: true` 是 Arco 的分組契約，缺了會被當成普通選項渲染出一列「正式版」文字。
 */
const groupOptions = computed(() => [
  {
    isGroup: true as const,
    label: '正式版',
    options: props.releases.map((r) => ({
      value: `release:${r.name}`,
      label: r.is_active ? `${r.name}（使用中）` : r.name,
    })),
  },
  {
    isGroup: true as const,
    label: '草稿',
    options: props.drafts.map((d) => ({ value: `draft:${d.version}`, label: d.version })),
  },
]);

/**
 * 拆版本鍵。
 *
 * 用 `indexOf(':')` 而非 `split(':')`——正式版名的字元集雖不含冒號，但只認第一個分隔符才是
 * 對前綴編碼的正確解讀，不依賴「名字裡不會有冒號」這個外部約定。
 */
function splitKey(key: string): { kind: string; name: string } {
  const sep = key.indexOf(':');
  return { kind: key.slice(0, sep), name: key.slice(sep + 1) };
}

watch(versionKey, async (key) => {
  if (!key) {
    text.value = '';
    return;
  }
  loading.value = true;
  try {
    const { kind, name } = splitKey(key);
    const res = kind === 'release' ? await getPromptRelease(name) : await getPromptDraft(name);
    text.value = res.system_prompt;
  } catch (error) {
    Message.error(error instanceof Error ? error.message : '載入版本全文失敗');
    text.value = '';
  } finally {
    loading.value = false;
  }
});
</script>

<template>
  <a-select
    v-model="versionKey"
    :size="size"
    class="w-full"
    :options="groupOptions"
    :loading="loading"
    :placeholder="placeholder"
  />
</template>

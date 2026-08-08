<script setup lang="ts">
import { IconSettings } from '@arco-design/web-vue/es/icon';
import { computed } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { PROVIDERS } from '@/features/settings/constants';
import type { LlmModelConfig } from '@/features/settings/types';

/**
 * 功能區選「用哪個模型配置」的下拉（5 個功能區 + 多模型跑批共用）。
 *
 * 旋鈕統一在設定 › LLM 設定 的具名配置庫維護，功能區頁面只需要「選哪一個」，故這裡只有一個下拉。
 *
 * 選項前的圓點＝該配置所屬供應商有沒有配 API token。
 * 沒 token 的配置仍然可選（不隱藏），但點是灰的：讓「為什麼跑不動」在選的當下就看得見，
 * 而不是送出後才收到 400。
 */
const props = withDefaults(
  defineProps<{
    /** 單選時＝config id；多選時＝config id 陣列。 */
    modelValue: string | string[];
    configs: LlmModelConfig[];
    providerHasToken: Record<string, boolean>;
    multiple?: boolean;
    /** 多選上限（對齊後端 `_MAX_ENTRIES_PER_GROUP`）；單選時忽略。 */
    limit?: number;
    disabled?: boolean;
    placeholder?: string;
    /** 是否顯示「管理 LLM 設定」入口（開設定抽屜的 LLM 設定分頁）。 */
    showManageLink?: boolean;
  }>(),
  {
    multiple: false,
    limit: 6,
    disabled: false,
    placeholder: '選擇模型配置',
    showManageLink: true,
  },
);

const emit = defineEmits<{ (e: 'update:modelValue', v: string | string[]): void }>();

const router = useRouter();
const route = useRoute();

const dotClass = (c: LlmModelConfig): string =>
  props.providerHasToken[c.provider] ? 'bg-[rgb(var(--green-6))]' : 'bg-[rgb(var(--gray-4))]';

/**
 * 依供應商分組，順序取 `PROVIDERS`（＝llm_model.json 的宣告序：OpenAI → Gemini → ByteDance），
 * 不用配置在 DB 的儲存序。沒有配置的供應商不產生空群組。
 */
const groups = computed(() =>
  PROVIDERS.map((p) => ({
    id: p.id,
    label: p.label,
    items: props.configs.filter((c) => c.provider === p.id),
  })).filter((g) => g.items.length > 0),
);

/**
 * 群組內的顯示名：去掉開頭的供應商段——群組標題已經寫著是哪一家，每個選項再帶一次是重複。
 * 收合後的顯示與多選標籤仍用 `:label`（完整名稱），因為那時看不到群組標題。
 */
const shortName = (c: LlmModelConfig, providerId: string): string => {
  const prefix = `${PROVIDERS.find((p) => p.id === providerId)?.short_label ?? ''} · `;
  return prefix.length > 3 && c.name.startsWith(prefix) ? c.name.slice(prefix.length) : c.name;
};

/** 開設定抽屜的 LLM 分頁（SettingsDrawer 監看 `?settings=` query，見該檔）。 */
const openSettings = (): void => {
  router.replace({ query: { ...route.query, settings: 'llm' } });
};

const anyMissingToken = computed(() => {
  const ids = Array.isArray(props.modelValue) ? props.modelValue : [props.modelValue];
  return props.configs.some((c) => ids.includes(c.id) && !props.providerHasToken[c.provider]);
});
</script>

<template>
  <div class="flex flex-col gap-1">
    <div class="flex items-center gap-2">
      <a-select
        class="min-w-0 flex-1"
        size="small"
        :model-value="modelValue"
        :multiple="multiple"
        :limit="multiple ? limit : undefined"
        :max-tag-count="2"
        :disabled="disabled"
        :placeholder="placeholder"
        @update:model-value="(v) => emit('update:modelValue', v as string | string[])"
      >
        <!-- 用 `<a-optgroup>` 元件式分組，不用 `:options` 陣列——後者的分組項必須帶 `isGroup: true`，
             漏掉會讓整組被當成單一選項（本專案踩過兩次）。元件式沒有這個坑。 -->
        <a-optgroup v-for="g in groups" :key="g.id" :label="g.label">
          <a-option v-for="c in g.items" :key="c.id" :value="c.id" :label="c.name">
            <!-- 名稱本身就是完整規格，不再另掛摘要——那會整行重複一次 -->
            <span class="inline-flex items-center gap-2">
              <span class="inline-block h-2 w-2 shrink-0 rounded-full" :class="dotClass(c)" />
              <span>{{ shortName(c, g.id) }}</span>
            </span>
          </a-option>
        </a-optgroup>
      </a-select>
      <!-- shrink-0 + nowrap：同列的 select 是 flex-1，容器變窄時會把按鈕文字擠到換行 -->
      <a-button
        v-if="showManageLink"
        type="text"
        size="small"
        class="shrink-0 whitespace-nowrap"
        @click="openSettings"
      ><template #icon><icon-settings /></template>
        管理 LLM 設定
      </a-button>
    </div>
    <span v-if="anyMissingToken" class="text-xs text-[rgb(var(--orange-6))]">
      選中的配置所屬供應商尚未設定 API token，執行會失敗——請先到「管理 LLM 設定」補上連線 token。
    </span>
  </div>
</template>

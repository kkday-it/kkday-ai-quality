<script setup lang="ts">
/**
 * 外部評論融合維度（評論系統 LLM 標籤：情緒分 + free_tag 面向標籤 + 來源 ext_lst_oid）：
 * 歸因列表「補充」區塊與歸因詳情抽屜共用，欄位與配色只留一份真相源。
 *
 * 兩種版型（`variant`）：
 * - `compact`（列表欄）：緊湊逐行，外層已有「補充」標籤故本元件不自帶標題。
 * - `detail`（詳情抽屜）：`a-descriptions` 完整鋪開，面向標籤逐條列出、附來源 ID。
 */
import { type ProblemRow } from '../constants';
import { sentimentClass, sentimentTagColor } from '../utils';

/** free_tag 單一面向（評論系統 LLM 標籤：面向名 + 面向分 + 命中詞）。 */
interface ExtFreeTag {
  tag_name?: string;
  tag_value?: string | number | null;
  tag_list?: string[];
}

const props = withDefaults(
  defineProps<{
    /** 該列資料（外部評論欄位皆為選填；無融合資料時由呼叫端以 `hasExternal` 決定不渲染）。 */
    record: ProblemRow;
    /** 版型：列表欄用 compact，詳情抽屜用 detail。 */
    variant?: 'compact' | 'detail';
  }>(),
  { variant: 'compact' },
);

/** 面向標籤陣列（後端回 JSON 陣列；缺值回空陣列避免模板逐處防禦）。 */
const freeTags = (): ExtFreeTag[] => (props.record.ext_free_tag as ExtFreeTag[] | undefined) ?? [];
</script>

<template>
  <!-- ── detail 版型：descriptions 完整鋪開（詳情抽屜）── -->
  <a-descriptions
    v-if="variant === 'detail'"
    title="外部評論"
    :column="1"
    size="medium"
    bordered
    :label-style="{ width: '88px' }"
  >
    <a-descriptions-item v-if="record.ext_sentiment" label="情緒分">
      <span class="font-semibold" :class="sentimentClass(record.ext_sentiment)">
        {{ record.ext_sentiment }} / 5
      </span>
    </a-descriptions-item>
    <a-descriptions-item v-if="freeTags().length" label="面向標籤">
      <!-- 每面向一行：面向分（上色數字）｜tag_name（按分上色 tag）｜tag_list（逐詞 Arco tag）-->
      <div
        v-for="(t, ti) in freeTags()"
        :key="ti"
        class="mb-1 flex flex-wrap items-center gap-x-1.5 gap-y-1 last:mb-0"
      >
        <span
          v-if="t.tag_value !== null && t.tag_value !== undefined && t.tag_value !== ''"
          class="font-semibold"
          :class="sentimentClass(t.tag_value)"
        >
          {{ t.tag_value }}
        </span>
        <a-tag size="small" :color="sentimentTagColor(t.tag_value)">{{ t.tag_name }}</a-tag>
        <a-tag v-for="(w, wi) in t.tag_list || []" :key="wi" size="small" color="gray">
          {{ w }}
        </a-tag>
      </div>
    </a-descriptions-item>
    <a-descriptions-item v-if="record.ext_lst_oid" label="來源 ID">
      <span class="text-xs text-[var(--color-text-2)]">ext#{{ record.ext_lst_oid }}</span>
    </a-descriptions-item>
  </a-descriptions>

  <!-- ── compact 版型：緊湊逐行（列表「補充」區塊內；外層已有標籤故不自帶標題）── -->
  <div v-else class="min-w-0 text-xs leading-relaxed">
    <div v-if="record.ext_sentiment" class="mb-0.5 flex flex-wrap items-center gap-x-2 gap-y-1">
      <span class="text-[var(--color-text-3)]">情緒分</span>
      <span class="font-semibold" :class="sentimentClass(record.ext_sentiment)">
        {{ record.ext_sentiment }} / 5
      </span>
    </div>
    <!-- 每面向一行：面向分（上色數字）｜tag_name（按分上色 tag）｜tag_list（逐詞 Arco tag）-->
    <div
      v-for="(t, ti) in freeTags()"
      :key="ti"
      class="mb-0.5 flex flex-wrap items-center gap-x-1.5 gap-y-1"
    >
      <span
        v-if="t.tag_value !== null && t.tag_value !== undefined && t.tag_value !== ''"
        class="font-semibold"
        :class="sentimentClass(t.tag_value)"
      >
        {{ t.tag_value }}
      </span>
      <a-tag size="small" :color="sentimentTagColor(t.tag_value)">{{ t.tag_name }}</a-tag>
      <a-tag v-for="(w, wi) in t.tag_list || []" :key="wi" size="small" color="gray">
        {{ w }}
      </a-tag>
    </div>
    <div v-if="record.ext_lst_oid" class="mt-0.5 text-[11px] text-[var(--color-text-3)]">
      ext#{{ record.ext_lst_oid }}
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 沙盒結果「左右並排對比卡片」（純展示佈局）：單 run 雙跑（基準 vs 草稿）與測試歷史
 * run-vs-run（A vs B）共用——同一套 source_id 表頭＋差異 tag＋兩欄各自 label/極性/條目
 * （差異高亮委派 SandboxPromptEntries diffAgainst）。單邊缺席（run-vs-run 對不齊）顯示
 * 空狀態；單邊初判失敗（error）顯示錯誤而非整條消失。
 */
import { computed } from 'vue';
import type { PromptSandboxItemResult, PromptSandboxVariantResult } from '@/api';
import SandboxPromptEntries from './SandboxPromptEntries.vue';

/** 一側的結果（單跑 item 或雙跑變體；null＝該側無此筆）。 */
type Side = (PromptSandboxVariantResult & { error?: string }) | PromptSandboxItemResult | null;

const props = defineProps<{
  /** 受測 item 的 source_id（表頭）。 */
  sourceId: string;
  /** 兩側是否有實質差異（表頭 tag + 邊框強調）。 */
  hasDiff: boolean;
  /** 左欄標籤（基準（選定版本）／A …）。 */
  leftLabel: string;
  /** 右欄標籤（草稿／B …）。 */
  rightLabel: string;
  /** 左側結果。 */
  left: Side;
  /** 右側結果。 */
  right: Side;
}>();

const entriesOf = (s: Side) => s?.prompts ?? [];
const leftEntries = computed(() => entriesOf(props.left));
const rightEntries = computed(() => entriesOf(props.right));
</script>

<template>
  <div class="rounded-lg border p-3" :class="hasDiff ? 'border-[rgb(var(--orange-4))]' : ''">
    <div class="mb-2 flex items-center gap-2">
      <span class="font-mono text-xs text-[var(--color-text-3)]">{{ sourceId }}</span>
      <a-tag v-if="hasDiff" size="small" color="orange">有差異</a-tag>
    </div>
    <div class="flex gap-3">
      <div class="min-w-0 flex-1">
        <div class="mb-1 text-xs font-medium text-[var(--color-text-3)]">
          {{ leftLabel }}<template v-if="left?.polarity"> · {{ left.polarity }}</template>
        </div>
        <a-alert v-if="left?.error" type="error">{{ left.error }}</a-alert>
        <SandboxPromptEntries
          v-else-if="left"
          :prompts="leftEntries"
          :diff-against="rightEntries"
          side="old"
        />
        <a-empty v-else description="無此筆" />
      </div>
      <div class="min-w-0 flex-1 border-l pl-3">
        <div class="mb-1 text-xs font-medium text-[var(--color-text-3)]">
          {{ rightLabel }}<template v-if="right?.polarity"> · {{ right.polarity }}</template>
        </div>
        <a-alert v-if="right?.error" type="error">{{ right.error }}</a-alert>
        <SandboxPromptEntries
          v-else-if="right"
          :prompts="rightEntries"
          :diff-against="leftEntries"
          side="new"
        />
        <a-empty v-else description="無此筆" />
      </div>
    </div>
  </div>
</template>

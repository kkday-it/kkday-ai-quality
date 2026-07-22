<script setup lang="ts">
/**
 * 沙盒測試單筆結果的逐 prompt 條目渲染（單跑與雙跑對比共用）：polarity 條目＝極性/情緒分/理由；
 * 域條目＝命中/棄權 + 全部歸因（l2 label + 信心 + reason）。
 *
 * 對比模式（傳 diffAgainst）：與另一側逐條比出差異並高亮——極性不同標橘；本側獨有的歸因標
 * 「新增」（綠，side='new'）或「消失」（紅，side='old'）；命中↔棄權翻轉時 tag 加強調框。
 */
import { computed } from 'vue';
import type { PromptSandboxItemResult } from '@/api';

type Entry = NonNullable<PromptSandboxItemResult['prompts']>[number];

const props = defineProps<{
  /** 本側逐 prompt 條目。 */
  prompts: Entry[];
  /** 對比另一側條目（給了即啟用差異高亮）。 */
  diffAgainst?: Entry[];
  /** 本側在對比中的角色：old＝基準（獨有歸因標「消失」紅）；new＝草稿（獨有標「新增」綠）。 */
  side?: 'old' | 'new';
}>();

/** 域條目判準：有 domain_label 欄位＝域 prompt 結果；否則為 polarity 條目。 */
const isDomainEntry = (p: Entry): boolean => p.domain_label !== undefined;

/** 另一側索引：prompt_id → {l2 集合, matched, polarity}（無對比模式＝null）。 */
const against = computed(() => {
  if (!props.diffAgainst) return null;
  const map = new Map<string, { l2s: Set<string>; matched: boolean; polarity?: string }>();
  for (const p of props.diffAgainst) {
    map.set(p.prompt_id, {
      l2s: new Set((p.attributions ?? []).map((a) => String(a.l2_code ?? ''))),
      matched: !!p.matched,
      polarity: p.polarity,
    });
  }
  return map;
});

/** 本側某歸因是否為對比差異（另一側同 prompt 無此 l2_code）。 */
const isDiffAttr = (promptId: string, l2Code: unknown): boolean => {
  const o = against.value?.get(promptId);
  return o != null && !o.l2s.has(String(l2Code ?? ''));
};

/** 命中/棄權與另一側翻轉。 */
const isFlipped = (p: Entry): boolean => {
  const o = against.value?.get(p.prompt_id);
  return o != null && o.matched !== !!p.matched;
};

/** 極性與另一側不同。 */
const isPolarityDiff = (p: Entry): boolean => {
  const o = against.value?.get(p.prompt_id);
  return o != null && o.polarity !== undefined && o.polarity !== p.polarity;
};

/** 差異標籤（新增＝本側多出來；消失＝本側還有、對側沒了——語意隨 side 反轉由呼叫端保證）。 */
const diffTag = computed(() => (props.side === 'old' ? '消失' : '新增'));
const diffTagColor = computed(() => (props.side === 'old' ? 'red' : 'green'));
</script>

<template>
  <div class="flex flex-col gap-2">
    <div
      v-for="(p, i) in prompts"
      :key="i"
      class="rounded border-l-2 border-[var(--color-border-3)] bg-[var(--color-fill-1)] px-2 py-1.5 text-xs"
    >
      <template v-if="isDomainEntry(p)">
        <div class="flex items-center gap-1.5">
          <a-tag
            size="small"
            :color="p.matched ? 'green' : 'gray'"
            :class="isFlipped(p) ? 'ring-1 ring-[rgb(var(--orange-6))]' : ''"
            >{{ p.matched ? '✅ 命中' : '⭕ 棄權' }}</a-tag
          >
          <span class="font-medium">{{ p.domain_label }}</span>
        </div>
        <div v-if="p.matched" class="mt-1 flex flex-col gap-1">
          <div
            v-for="(a, k) in p.attributions ?? []"
            :key="k"
            class="flex items-start gap-1.5"
            :class="
              isDiffAttr(p.prompt_id, a.l2_code) ? 'rounded bg-[var(--color-fill-2)] px-1' : ''
            "
          >
            <a-tag
              v-if="isDiffAttr(p.prompt_id, a.l2_code)"
              size="small"
              :color="diffTagColor"
              class="shrink-0"
              >{{ diffTag }}</a-tag
            >
            <span class="shrink-0">{{ a.l2_label || a.l2_code }}</span>
            <span class="shrink-0 font-mono text-[11px] text-[var(--color-text-3)]"
              >{{ Math.round((a.confidence ?? 0) * 100) }}%</span
            >
            <span v-if="a.reason" class="text-[var(--color-text-2)]">{{ a.reason }}</span>
          </div>
        </div>
        <div v-else-if="p.abstain_reason" class="mt-1 text-[var(--color-text-2)]">
          {{ p.abstain_reason }}
        </div>
      </template>
      <template v-else>
        <div class="flex items-center gap-1.5">
          <a-tag size="small" color="arcoblue">極性</a-tag>
          <span
            class="font-medium"
            :class="isPolarityDiff(p) ? 'text-[rgb(var(--orange-6))]' : ''"
            >{{ p.polarity }}</span
          >
          <a-tag v-if="isPolarityDiff(p)" size="small" color="orange">與另一側不同</a-tag>
          <span class="ml-auto font-mono text-[11px] text-[var(--color-text-3)]"
            >情緒 {{ p.sentiment_score }}</span
          >
        </div>
        <div v-if="p.reason" class="mt-1 text-[var(--color-text-2)]">{{ p.reason }}</div>
      </template>
    </div>
  </div>
</template>

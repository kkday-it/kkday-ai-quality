<script setup lang="ts">
import { nextTick, ref, watch } from 'vue';
import type { LlmPingResult } from '@/api';
import Terminal from './Terminal.vue';
import { PROVIDERS } from '@/features/settings/constants';
import type { LlmKnobs } from '@/features/settings/types';

/** `useLlmConfigTest` 測試結果的呈現元件：純顯示，不含請求邏輯（邏輯見該 composable）。
 * 同時展示「送出的配置參數」與「LLM 實際反饋」——只看連線通不通無法判斷 reasoning_effort /
 * temperature 等旋鈕是否真的對該 model 生效，故兩者並列輸出。 */
type Knobs = LlmKnobs;

const props = defineProps<{
  result: LlmPingResult | null;
  provider: string;
  knobs: Knobs;
}>();

const termRef = ref<InstanceType<typeof Terminal>>();

const ANSI = {
  reset: '\x1b[0m',
  green: '\x1b[32m',
  red: '\x1b[31m',
  cyan: '\x1b[36m',
  magenta: '\x1b[35m',
  dim: '\x1b[90m',
  yellow: '\x1b[33m',
} as const;

/** 組出「配置參數」行：只列非 default 的旋鈕值，避免一長串 default 淹沒真正被覆寫的值。 */
function knobsLine(): string {
  const providerLabel = PROVIDERS.find((p) => p.id === props.provider)?.label ?? props.provider;
  const parts = [`provider=${providerLabel}`, `model=${props.knobs.model || '(未選)'}`];
  if (props.knobs.thinking && props.knobs.thinking !== 'default') {
    parts.push(`thinking=${props.knobs.thinking}`);
  }
  parts.push(
    `reasoning_effort=${
      props.knobs.reasoning_effort && props.knobs.reasoning_effort !== 'default'
        ? props.knobs.reasoning_effort
        : 'default（不送，用 API 預設）'
    }`,
  );
  parts.push(`temperature=${props.knobs.temperature ?? 'API 預設'}`);
  return parts.join('  ');
}

watch(
  () => props.result,
  async (r) => {
    if (!r) return;
    await nextTick();
    const t = termRef.value;
    if (!t) return;
    t.clear();
    const head = r.ok ? `${ANSI.green}● 連線成功` : `${ANSI.red}● 連線失敗`;
    const lat = r.latency_ms ? ` ${ANSI.dim}· ${r.latency_ms}ms` : '';
    t.writeln(`${head}${lat}${ANSI.reset}`);
    t.writeln(`${ANSI.yellow}# 配置參數${ANSI.reset}`);
    t.writeln(`${ANSI.dim}${knobsLine()}${ANSI.reset}`);
    t.writeln(`${ANSI.dim}# 實際送出：${r.model ?? ''} @ ${r.base_url ?? ''}${ANSI.reset}`);
    t.writeln(`${ANSI.yellow}# LLM 反饋${ANSI.reset}`);
    if (r.sent) t.writeln(`${ANSI.green}➜${ANSI.reset} ${ANSI.cyan}send${ANSI.reset} ${r.sent}`);
    if (r.reply) t.writeln(`${ANSI.magenta}←${ANSI.reset} ${r.reply}`);
    if (r.tokens) t.writeln(`${ANSI.dim}tokens=${r.tokens}${ANSI.reset}`);
    if (r.error) t.writeln(`${ANSI.red}✗ ${r.error}${ANSI.reset}`);
  },
);
</script>

<template>
  <Terminal v-if="result" ref="termRef" class="mt-2" height="9rem" />
</template>

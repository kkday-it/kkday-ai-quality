<script setup lang="ts">
import { computed, ref } from 'vue';
import { Message } from '@arco-design/web-vue';
import { patchStatus } from '@/api';
import {
  ACTION_LABEL as ALABEL,
  STATUS_LABEL as SLABEL,
  STATUS_COLOR as SCOLOR,
} from '../constants';

// f＝list_findings 列（judgments typed 欄 + 巢狀 finding DTO）。
const props = defineProps<{ f: any }>();

// 本地狀態（optimistic 更新）：避免直接 mutate prop（vue/no-mutating-props）；模板用 localStatus 顯示
const localStatus = ref<string>(props.f.status);

const conf = computed(() => Number(props.f.conf_value ?? 0));
const confLevel = computed(() => (conf.value >= 0.85 ? 'hi' : conf.value >= 0.7 ? 'mid' : 'lo'));

// 信心徽章配色：完整 class 字串（非拼接）確保 Tailwind JIT 掃得到、不被 purge。
const CONF_CLASS = {
  hi: 'text-[#00875a] bg-[#e3fcef]',
  mid: 'text-[#165dff] bg-[#e8f3ff]',
  lo: 'text-[#d4380d] bg-[#fff1f0]',
} as const;
const confClass = computed(() => CONF_CLASS[confLevel.value]);

// 歸因麵包屑（L1 › L2 › L3；取 typed 欄 label）
const attrPath = computed(
  () => [props.f.l1_label, props.f.l2_label, props.f.l3_label].filter(Boolean).join(' › ') || '未歸因',
);

const setStatus = async (s: string) => {
  try {
    await patchStatus(props.f.finding_id, s);
    localStatus.value = s;
    Message.success('狀態已更新');
  } catch (e) {
    // 修靜默失敗：點按鈕後若後端 4xx/網路錯，給使用者明確回饋（原本完全無提示）
    Message.error(`更新失敗：${e instanceof Error ? e.message : String(e)}`);
  }
};
</script>

<template>
  <a-card class="mb-3">
    <!-- 突顯識別列：信心 / 商品 OID（有才顯示）/ 狀態 -->
    <div class="flex items-center gap-3 rounded-lg bg-[#f7f8fa] px-2.5 py-1.5">
      <span class="rounded-md px-2 py-px text-sm font-bold" :class="confClass"
        >信心 {{ conf.toFixed(2) }}</span
      >
      <span v-if="f.prod_oid" class="font-mono text-[13px] font-semibold text-[#1d2129]"
        ><span class="mr-0.5 text-[11px] font-normal text-[#86909c]">商品</span>
        {{ f.prod_oid }}</span
      >
      <a-tag :color="SCOLOR[localStatus]" class="ml-auto">{{
        SLABEL[localStatus] || localStatus
      }}</a-tag>
    </div>

    <a-space wrap class="mb-1 mt-2">
      <a-tag color="arcoblue">{{ f.dimension }}</a-tag>
      <a-tag v-if="f.is_primary" color="purple" bordered>主要</a-tag>
    </a-space>

    <!-- 歸因分類麵包屑（L1 › L2 › L3）-->
    <div class="my-1 text-[13px] font-medium text-[#1d2129]">{{ attrPath }}</div>
    <!-- 反饋摘要 -->
    <div class="my-1 text-sm leading-[1.55]">{{ f.summary }}</div>
    <!-- 佐證原文 -->
    <div v-if="f.evidence" class="mt-1.5 text-[12.5px] leading-normal text-[#86909c]">
      📄 佐證：{{ f.evidence }}
    </div>

    <div class="mt-2.5 flex flex-wrap items-center gap-2">
      <span class="text-xs text-[#86909c]"
        >建議動作：<b>{{ ALABEL[f.action] || f.action }}</b></span
      >
      <span class="ml-auto"></span>
      <a-button size="mini" type="outline" status="success" @click="setStatus('confirmed')"
        >確認</a-button
      >
      <a-button size="mini" type="outline" @click="setStatus('dismissed')">忽略</a-button>
      <a-button size="mini" type="outline" status="warning" @click="setStatus('fixed')"
        >已修</a-button
      >
    </div>
  </a-card>
</template>

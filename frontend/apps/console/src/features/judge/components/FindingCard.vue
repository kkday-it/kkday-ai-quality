<script setup lang="ts">
import { computed } from 'vue';
import { patchStatus } from '@/api';
import {
  VERDICT_LABEL as VLABEL,
  VERDICT_COLOR as VCOLOR,
  FIELD_LABEL as FLABEL,
  ACTION_LABEL as ALABEL,
  CHANNEL_LABEL as CHANNEL,
  STATUS_LABEL as SLABEL,
  STATUS_COLOR as SCOLOR,
} from '../constants';

const props = defineProps<{ f: any }>();

const conf = computed(() => Number(props.f.confidence ?? 0));
const confLevel = computed(() => (conf.value >= 0.85 ? 'hi' : conf.value >= 0.7 ? 'mid' : 'lo'));

// 信心徽章配色：完整 class 字串（非拼接）確保 Tailwind JIT 掃得到、不被 purge。
const CONF_CLASS = {
  hi: 'text-[#00875a] bg-[#e3fcef]',
  mid: 'text-[#165dff] bg-[#e8f3ff]',
  lo: 'text-[#d4380d] bg-[#fff1f0]',
} as const;
const confClass = computed(() => CONF_CLASS[confLevel.value]);

const setStatus = async (s: string) => {
  await patchStatus(props.f.finding_id, s);
  props.f.status = s;
};
</script>

<template>
  <a-card class="mb-3">
    <!-- 突顯識別列：信心 / 商品·方案·訂單·供應商 OID（有才顯示）/ 狀態 -->
    <div class="flex items-center gap-3 rounded-lg bg-[#f7f8fa] px-2.5 py-1.5">
      <span class="rounded-md px-2 py-px text-sm font-bold" :class="confClass">信心 {{ conf.toFixed(2) }}</span>
      <span v-if="f.prod_oid" class="font-mono text-[13px] font-semibold text-[#1d2129]"><span class="mr-0.5 text-[11px] font-normal text-[#86909c]">商品</span> {{ f.prod_oid }}</span>
      <span v-if="f.pkg_oid" class="font-mono text-[13px] font-semibold text-[#1d2129]"><span class="mr-0.5 text-[11px] font-normal text-[#86909c]">方案</span> {{ f.pkg_oid }}</span>
      <span v-if="f.order_oid" class="font-mono text-[13px] font-semibold text-[#1d2129]"><span class="mr-0.5 text-[11px] font-normal text-[#86909c]">訂單</span> {{ f.order_oid }}</span>
      <span v-if="f.supplier_oid" class="font-mono text-[13px] font-semibold text-[#1d2129]"><span class="mr-0.5 text-[11px] font-normal text-[#86909c]">供應商</span> {{ f.supplier_oid }}</span>
      <a-tag :color="SCOLOR[f.status]" class="ml-auto">{{ SLABEL[f.status] || f.status }}</a-tag>
    </div>

    <a-space wrap class="mb-1 mt-2">
      <a-tag color="arcoblue">{{ f.dimension }}</a-tag>
      <a-tag :color="VCOLOR[f.verdict]">{{ VLABEL[f.verdict] || f.verdict }}</a-tag>
      <a-tag v-if="f.suspected_field && f.suspected_field !== 'none'" color="purple">{{ FLABEL[f.suspected_field] || f.suspected_field }}</a-tag>
      <a-tag v-if="f.is_primary" color="purple" bordered>主要</a-tag>
    </a-space>

    <div class="my-1 text-sm leading-[1.55]">{{ f.problem_summary }}</div>
    <div v-if="f.evidence_quote" class="mt-1.5 text-[12.5px] leading-normal text-[#86909c]">📄 目前頁面：{{ f.evidence_quote }}</div>
    <div v-if="f.ground_truth_quote" class="mt-2 rounded-lg border border-[#a3e8dd] bg-[#e8fffb] px-[11px] py-2 text-[12.5px] leading-normal">
      <b class="text-[#0f9b8e]">✅ 客服標準答案（待補事實）：</b>{{ f.ground_truth_quote }}
    </div>

    <div class="mt-2 flex flex-wrap gap-1.5">
      <span class="rounded-md bg-[#f2f3f5] px-2 py-px text-[11.5px] text-[#4e5969]">📥 感知層：{{ CHANNEL[f.source_channel] || '其他' }}<template v-if="f.source_system"> · {{ f.source_system }}</template></span>
      <span v-if="f.owner_role" class="rounded-md bg-[#f2f3f5] px-2 py-px text-[11.5px] text-[#4e5969]">👤 {{ f.owner_role }}</span>
      <span v-if="f.exec_platform" class="rounded-md bg-[#f2f3f5] px-2 py-px text-[11.5px] text-[#4e5969]">🛠 {{ f.exec_platform }}</span>
    </div>

    <div class="mt-2.5 flex flex-wrap items-center gap-2">
      <span v-if="f.verdict === 'contract_breach'" class="text-xs text-[#fb923c]">⚠ 內容合規但承諾履約不符 → 計點違規 + 要求供應商改善（ERC）</span>
      <span v-else-if="f.verdict === 'customer_misread'" class="text-xs text-[#86909c]">內容其實清楚 → 不需修改（呈現/UX 議題）</span>
      <span v-else-if="f.verdict === 'escalate_ops'" class="text-xs text-[#86909c]">非內容問題 → 轉其他單位</span>
      <template v-else-if="f.writer_handoff">
        <a-button size="mini" type="primary">✎ 用 writer 重生{{ FLABEL[f.suspected_field] }}</a-button>
        <span class="text-xs text-[#86909c]">可重生（改寫既有事實），結果供確認不自動寫回</span>
      </template>
      <span v-else class="text-xs text-[#fb923c]">⛔ 缺事實 → 需 PM 手動補（不可自動重生）</span>
    </div>

    <div class="mt-2.5 flex flex-wrap items-center gap-2">
      <span class="text-xs text-[#86909c]">建議動作：<b>{{ ALABEL[f.recommended_action] || f.recommended_action }}</b></span>
      <span class="ml-auto"></span>
      <a-button size="mini" type="outline" status="success" @click="setStatus('confirmed')">確認</a-button>
      <a-button size="mini" type="outline" @click="setStatus('dismissed')">忽略</a-button>
      <a-button size="mini" type="outline" status="warning" @click="setStatus('fixed')">已修</a-button>
    </div>
  </a-card>
</template>

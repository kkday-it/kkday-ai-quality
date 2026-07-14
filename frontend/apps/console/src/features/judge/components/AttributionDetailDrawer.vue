<script setup lang="ts">
/**
 * 判決詳情抽屜（原 AttributionList 內 modal 抽出）：完整展示單一反饋的
 * 原文 → 關聯資料 → 每條歸因全欄位（分類路徑/信心含原始值/階段/覆核狀態/摘要多語系/
 * 逐字佐證/建議行動/負責單位/真值/finding_id）。純展示、資料取自列上 attributions，零額外請求；
 * 全部走 Arco 現成組件（a-drawer / a-descriptions / a-tag / a-rate / a-typography）。
 */
import {
  ACTION_LABEL,
  POLARITY_LABELS,
  STAGE_LABELS,
  STATUS_COLOR,
  STATUS_LABEL,
  TIER_LABELS,
  TRAVELLER_TYPE_LABELS,
  type Attribution,
  type ProblemRow,
} from '../constants';
import { fmtDt } from '../utils';

const visible = defineModel<boolean>('visible', { default: false });
defineProps<{ row: ProblemRow | null }>();

/** 傾向語義色（與列表一致；小常數各處自帶，未達 Rule of Three 不抽 SSOT）。 */
const POLARITY_COLOR: Record<string, string> = {
  positive: 'green',
  negative: 'red',
  neutral: 'gray',
};

/** 判決階段語義色（同列表：已判決綠 / 待覆核橙 / 待數據補充藍）。 */
const STAGE_COLOR: Record<string, string> = {
  judged: 'green',
  pending_review: 'orange',
  pending_data: 'arcoblue',
};

/** 信心分層語義色（auto_accept 可採信綠 / jury 需覆核橙 / needs_review 必人工紅）。 */
const TIER_COLOR: Record<string, string> = {
  auto_accept: 'green',
  jury: 'orange',
  needs_review: 'red',
};

/** 歸因路徑「L1 › L2 › L3」；未歸因回占位文字。 */
const attrPath = (a: Attribution): string =>
  [a.l1?.label, a.l2?.label, a.l3?.label].filter(Boolean).join(' › ') || '未歸因';

/** 最深層 code（L3 → L2 → L1 取第一個非空），路徑旁小字輔助定位規則樹。 */
const attrCode = (a: Attribution): string => a.l3?.code || a.l2?.code || a.l1?.code || '';

/** 欄位缺值顯示（'—'）。 */
const cell = (v: unknown): string => (v === null || v === undefined || v === '' ? '—' : String(v));

/** summary_langs 中「非 zh-tw」的其他語系（原文語言摘要，zh-tw 已是主顯示）。 */
const otherLangs = (a: Attribution): [string, string][] =>
  Object.entries(a.content?.summary_langs || {}).filter(([lang]) => lang !== 'zh-tw');
</script>

<template>
  <a-drawer
    v-model:visible="visible"
    :width="640"
    :footer="false"
    unmount-on-close
    :title="`判決詳情 · #${row?.source_record_id ?? row?.source_id ?? ''}`"
  >
    <div v-if="row" class="flex flex-col gap-4">
      <!-- ① 反饋原文：星等 + 傾向 + 標題 + 全文 + ID·時間 -->
      <div class="rounded-md bg-[var(--color-fill-1)] p-3">
        <div class="mb-1 flex flex-wrap items-center gap-x-2 gap-y-1">
          <a-rate
            v-if="row.score !== null && row.score !== undefined && row.score !== ''"
            :model-value="Number(row.score) || 0"
            readonly
            :count="5"
            class="text-sm"
          />
          <a-tag v-if="row.polarity" size="small" :color="POLARITY_COLOR[row.polarity]">
            {{ POLARITY_LABELS[row.polarity] || row.polarity }}
          </a-tag>
          <span v-if="row.title" class="text-sm font-medium text-[var(--color-text-1)]">
            {{ row.title }}
          </span>
        </div>
        <div class="whitespace-pre-wrap text-sm leading-relaxed text-[var(--color-text-1)]">
          {{ row.content || '（無評論內容）' }}
        </div>
        <div class="mt-1.5 text-[11px] text-[var(--color-text-3)]">
          #{{ row.source_record_id || row.source_id || '—' }} ·
          {{ fmtDt(String(row.occurred_at ?? '')) || '—' }}
        </div>
      </div>

      <!-- ② 關聯資料：與列表「關聯資料」欄同源欄位，descriptions 完整鋪開 -->
      <a-descriptions
        title="關聯資料"
        :column="1"
        size="medium"
        bordered
        :label-style="{ width: '88px' }"
      >
        <a-descriptions-item label="訂單">
          <div class="font-medium">{{ cell(row.order_mid) }}</div>
          <div class="text-xs text-[var(--color-text-2)]">
            OID {{ cell(row.order_oid) }} · 出發 {{ fmtDt(String(row.go_date ?? ''), true) || '—' }}
          </div>
        </a-descriptions-item>
        <a-descriptions-item label="商品">
          <div v-if="row.prod_name" class="font-medium">{{ row.prod_name }}</div>
          <div class="text-xs text-[var(--color-text-2)]">
            OID {{ cell(row.prod_oid) }} · {{ cell(row.product_category_main) }} ·
            {{ cell(row.lang) }}
          </div>
        </a-descriptions-item>
        <a-descriptions-item label="方案">
          <div v-if="row.package_name">{{ row.package_name }}</div>
          <div class="text-xs text-[var(--color-text-2)]">OID {{ cell(row.pkg_oid) }}</div>
        </a-descriptions-item>
        <a-descriptions-item label="供應商">{{ cell(row.supplier_oid) }}</a-descriptions-item>
        <a-descriptions-item label="旅客">
          <a-tag v-if="row.traveller_type" size="small" color="arcoblue">
            {{ TRAVELLER_TYPE_LABELS[String(row.traveller_type)] || row.traveller_type }}
          </a-tag>
          <span v-if="row.member_uuid" class="ml-1.5 break-all text-xs text-[var(--color-text-2)]">
            {{ row.member_uuid }}
          </span>
          <span v-if="!row.traveller_type && !row.member_uuid">—</span>
        </a-descriptions-item>
      </a-descriptions>

      <!-- ③ 每條歸因：全欄位 descriptions（標題列帶主歸因/覆核狀態/真值徽章）-->
      <template v-if="row.attributions && row.attributions.length">
        <a-descriptions
          v-for="(a, ai) in row.attributions"
          :key="a.finding_id || ai"
          :column="1"
          size="medium"
          bordered
          :label-style="{ width: '88px' }"
        >
          <template #title>
            <div class="flex flex-wrap items-center gap-1.5">
              <span>歸因 {{ ai + 1 }}</span>
              <a-tag v-if="a.is_primary && row.attributions.length > 1" size="small" color="purple"
                >主歸因</a-tag
              >
              <a-tag v-if="a.status" size="small" :color="STATUS_COLOR[a.status]">
                {{ STATUS_LABEL[a.status] || a.status }}
              </a-tag>
            </div>
          </template>
          <a-descriptions-item label="歸因分類">
            <span>{{ attrPath(a) }}</span>
            <span v-if="attrCode(a)" class="ml-1.5 text-xs text-[var(--color-text-3)]">{{
              attrCode(a)
            }}</span>
          </a-descriptions-item>
          <a-descriptions-item label="信心 / 分層">
            <b>{{
              typeof a.confidence?.value === 'number' ? a.confidence.value.toFixed(2) : '—'
            }}</b>
            <a-tag
              v-if="a.confidence?.tier"
              size="small"
              :color="TIER_COLOR[a.confidence.tier]"
              class="ml-1.5"
            >
              {{ TIER_LABELS[a.confidence.tier] || a.confidence.tier }}
            </a-tag>
            <!-- 校準後 value ≠ LLM 原始 raw 時並列原始值，供覆核者判讀校準幅度 -->
            <span
              v-if="
                typeof a.confidence?.raw === 'number' && a.confidence.raw !== a.confidence.value
              "
              class="ml-1.5 text-xs text-[var(--color-text-3)]"
            >
              原始 {{ a.confidence.raw.toFixed(2) }}
            </span>
          </a-descriptions-item>
          <a-descriptions-item label="判決階段">
            <a-tag v-if="a.stage" size="small" :color="STAGE_COLOR[a.stage]">
              {{ STAGE_LABELS[a.stage] || a.stage }}
            </a-tag>
            <span v-else>—</span>
          </a-descriptions-item>
          <a-descriptions-item label="判決模型">
            <a-tag v-if="a.model" size="small" color="purple">{{ a.model }}</a-tag>
            <span v-else>—</span>
          </a-descriptions-item>
          <a-descriptions-item label="反饋摘要">
            <div>{{ a.content?.summary || '—' }}</div>
            <!-- 其他語系摘要（原文語言版本；zh-tw 已為主顯示）-->
            <div
              v-for="[lang, text] in otherLangs(a)"
              :key="lang"
              class="mt-0.5 text-xs text-[var(--color-text-3)]"
            >
              <a-tag size="small" class="mr-1">{{ lang }}</a-tag
              >{{ text }}
            </div>
          </a-descriptions-item>
          <a-descriptions-item label="逐字佐證">
            <blockquote
              v-if="a.content?.evidence"
              class="m-0 border-l-2 border-[rgb(var(--primary-4))] pl-2 text-xs leading-relaxed text-[var(--color-text-2)]"
            >
              {{ a.content.evidence }}
            </blockquote>
            <span v-else>—</span>
          </a-descriptions-item>
          <a-descriptions-item label="建議行動">
            {{ a.content?.action ? ACTION_LABEL[a.content.action] || a.content.action : '—' }}
          </a-descriptions-item>
          <a-descriptions-item v-if="a.owner" label="負責單位">{{ a.owner }}</a-descriptions-item>
          <a-descriptions-item label="finding">
            <span class="break-all text-xs text-[var(--color-text-3)]">{{
              a.finding_id || '—'
            }}</span>
          </a-descriptions-item>
        </a-descriptions>
      </template>
      <a-empty v-else description="此列尚無歸因（未判 / 正向不歸因）" />
    </div>
  </a-drawer>
</template>

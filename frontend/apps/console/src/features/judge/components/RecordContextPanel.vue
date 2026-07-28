<script setup lang="ts">
/**
 * 關聯資料共用區塊（訂單／商品／方案／供應商／進線／組織分工／旅客／客服標籤）：
 * 歸因列表「關聯資料」欄與歸因詳情抽屜的同源資訊，收斂為單一元件，欄位歸屬只留一份真相源，
 * 商品評論／售前售後進線（及其餘反饋來源）皆共用。
 *
 * 兩種版型（`variant`）僅差在排版與資訊密度，欄位語義一致：
 * - `compact`（列表欄）：左小標籤 + 右內容的緊湊列，段落依 `sections` 白名單裁剪。
 * - `detail`（詳情抽屜）：`a-descriptions` 完整鋪開，額外顯示列表放不下的次要欄位
 *   （利潤／語系／建立時間／商品時區／BD TAG code／出發日差／客服標籤）。
 */
import {
  ALL_CONTEXT_SECTIONS,
  SECTION_LABEL_CLASS,
  BUCKET_COLORS,
  BUCKET_LABELS,
  INBOUND_TRIP_STAGE_LABELS,
  MSG_HANDLER_BUCKET_LABELS,
  TRAVELLER_TYPE_LABELS,
  type ContextSection,
  type ProblemRow,
} from '../constants';
import { fmtDt } from '../utils';

const props = withDefaults(
  defineProps<{
    /** 該列資料（`_enrich_problem` 回傳；非該來源的欄位恆為空，各段落防禦式顯示「—」）。 */
    record: ProblemRow;
    /** 要顯示的段落白名單（見 source-schema.constant `contextSections` /
     *  `supplementSections`）；未給＝ `ALL_CONTEXT_SECTIONS` 全段落（詳情抽屜用）。 */
    sections?: ContextSection[];
    /** 版型：列表欄用 compact，詳情抽屜用 detail。 */
    variant?: 'compact' | 'detail';
    /** detail 版型的 descriptions 標題（compact 版型不顯示標題，由外層區塊自帶）。 */
    title?: string;
    /** compact 版型是否顯示各段左側小標籤；巢狀於外層已有標籤的區塊內（如反饋內容欄的「補充」）時傳
     *  false，避免標籤層層重複。detail 版型的 descriptions label 不受此影響。 */
    showLabels?: boolean;
  }>(),
  { sections: () => ALL_CONTEXT_SECTIONS, variant: 'compact', title: '關聯資料', showLabels: true },
);

/** 段落是否顯示（schema 白名單）。 */
const hasSection = (s: ContextSection): boolean => props.sections.includes(s);

/** 欄位缺值防禦顯示（'—'）；部分來源（mixpanel）OID 為 JSON 陣列字串 `["x"]` → 攤平顯示。 */
const cell = (v: unknown): string => {
  if (v === null || v === undefined || v === '') return '—';
  const s = String(v);
  if (s.startsWith('[') && s.endsWith(']')) {
    try {
      const arr = JSON.parse(s);
      if (Array.isArray(arr)) return arr.length ? arr.map(String).join('、') : '—';
    } catch {
      /* 非 JSON 陣列 → 原樣顯示 */
    }
  }
  return s;
};
</script>

<template>
  <!-- ── detail 版型：a-descriptions 完整鋪開（詳情抽屜）── -->
  <a-descriptions
    v-if="variant === 'detail'"
    :title="title"
    :column="1"
    size="medium"
    bordered
    :label-style="{ width: '88px' }"
  >
    <!-- 進線屬性（conversations 專屬：分桶/行程階段/處理方/出發日差，其餘來源無值不顯示）-->
    <a-descriptions-item
      v-if="
        hasSection('inbound') &&
        (record.bucket || record.trip_stage || record.msg_handler_bucket || record.godate_diff)
      "
      label="進線屬性"
    >
      <div class="flex flex-wrap items-center gap-1.5">
        <a-tag
          v-if="record.bucket"
          size="small"
          :color="BUCKET_COLORS[String(record.bucket)] || 'gray'"
        >
          {{ BUCKET_LABELS[String(record.bucket)] || record.bucket }}
        </a-tag>
        <a-tag v-if="record.trip_stage" size="small" color="arcoblue">
          {{ INBOUND_TRIP_STAGE_LABELS[String(record.trip_stage)] || record.trip_stage }}
        </a-tag>
        <span v-if="record.msg_handler_bucket" class="text-xs text-[var(--color-text-2)]">
          處理方:
          {{
            MSG_HANDLER_BUCKET_LABELS[String(record.msg_handler_bucket)] ||
            record.msg_handler_bucket
          }}
        </span>
        <span v-if="record.godate_diff" class="text-xs text-[var(--color-text-2)]">
          出發日差: {{ record.godate_diff }}
        </span>
      </div>
    </a-descriptions-item>
    <a-descriptions-item v-if="hasSection('order')" label="訂單">
      <div class="font-medium">{{ cell(record.order_mid) }}</div>
      <div class="text-xs text-[var(--color-text-2)]">
        OID: {{ cell(record.order_oid) }} · 出發:
        {{ fmtDt(String(record.go_date ?? ''), true) || '—' }}
      </div>
      <!-- 進線專屬：訂單狀態（其餘來源恆空不顯示）/ 金額 / 利潤 -->
      <div
        v-if="record.order_status_now || record.order_price || record.order_profit"
        class="text-xs text-[var(--color-text-2)]"
      >
        <template v-if="record.order_status_now">狀態: {{ record.order_status_now }} · </template>
        金額: {{ cell(record.order_price) }} · 利潤: {{ cell(record.order_profit) }}
      </div>
      <div
        v-if="record.order_lang || record.order_create_source_code || record.order_create_time"
        class="text-xs text-[var(--color-text-2)]"
      >
        語系: {{ cell(record.order_lang) }} · 建立來源: {{ cell(record.order_create_source_code) }} ·
        建立時間: {{ fmtDt(String(record.order_create_time ?? '')) || '—' }}
      </div>
    </a-descriptions-item>
    <a-descriptions-item v-if="hasSection('product')" label="商品">
      <div v-if="record.prod_name" class="font-medium">
        {{ record.prod_name }}
      </div>
      <div class="text-xs text-[var(--color-text-2)]">
        OID: {{ cell(record.prod_oid) }} · 語系: {{ cell(record.lang) }}
      </div>
      <!-- 進線專屬：商品時區（其餘來源恆空不顯示）-->
      <div v-if="record.product_tz" class="text-xs text-[var(--color-text-2)]">
        時區: {{ cell(record.product_tz) }}
      </div>
    </a-descriptions-item>
    <a-descriptions-item v-if="hasSection('package')" label="方案">
      <div v-if="record.package_name">{{ record.package_name }}</div>
      <div class="text-xs text-[var(--color-text-2)]">OID: {{ cell(record.pkg_oid) }}</div>
    </a-descriptions-item>
    <a-descriptions-item v-if="hasSection('supplier')" label="供應商">
      <div v-if="record.supplier_name" class="font-medium">{{ record.supplier_name }}</div>
      <div class="text-xs text-[var(--color-text-2)]">OID: {{ cell(record.supplier_oid) }}</div>
    </a-descriptions-item>
    <!-- 組織分工：垂直分類／BD TAG／PM（bd_tag_vertical 系統，product_reviews 與 conversations 皆有值）-->
    <a-descriptions-item
      v-if="
        hasSection('org') &&
        (record.vertical || record.bd_tag || record.bd_tag_cd || record.bd_tag_note || record.PM)
      "
      label="組織分工"
    >
      <div v-if="record.vertical" class="mb-1">
        <a-tag size="small" color="cyan">{{ record.vertical }}</a-tag>
      </div>
      <div
        v-if="record.bd_tag || record.bd_tag_cd || record.bd_tag_note"
        class="text-xs text-[var(--color-text-2)]"
      >
        BD TAG: {{ cell(record.bd_tag) }}（{{ cell(record.bd_tag_cd) }}） {{ cell(record.bd_tag_note) }}
      </div>
      <div v-if="record.PM" class="text-xs text-[var(--color-text-2)]">PM: {{ record.PM }}</div>
    </a-descriptions-item>
    <!-- 客服標籤（conversations 專屬；其餘來源恆空不顯示）-->
    <a-descriptions-item
      v-if="
        hasSection('inbound') &&
        (record.cs_tag_name || record.cs_tag_oid || record.user_message_count)
      "
      label="客服標籤"
    >
      <span v-if="record.cs_tag_name">{{ record.cs_tag_name }}</span>
      <span v-if="record.cs_tag_oid" class="ml-1.5 text-xs text-[var(--color-text-2)]"
        >({{ record.cs_tag_oid }})</span
      >
      <span v-if="record.user_message_count" class="ml-1.5 text-xs text-[var(--color-text-2)]">
        訊息數: {{ record.user_message_count }}
      </span>
    </a-descriptions-item>
    <a-descriptions-item v-if="hasSection('traveller')" label="旅客">
      <a-tag v-if="record.traveller_type" size="small" color="arcoblue">
        {{ TRAVELLER_TYPE_LABELS[String(record.traveller_type)] || record.traveller_type }}
      </a-tag>
      <span v-if="record.member_uuid" class="ml-1.5 break-all text-xs text-[var(--color-text-2)]">
        會員: {{ record.member_uuid }}
      </span>
      <span v-if="!record.traveller_type && !record.member_uuid">—</span>
    </a-descriptions-item>
  </a-descriptions>

  <!-- ── compact 版型：左小標籤 + 右內容的緊湊列（列表「關聯資料」欄）── -->
  <div v-else class="flex flex-col gap-1 py-1 text-xs leading-relaxed">
    <!-- 訂單 -->
    <div v-if="hasSection('order')" class="flex gap-1.5">
      <span v-if="showLabels" :class="SECTION_LABEL_CLASS">訂單</span>
      <div class="min-w-0">
        <div class="font-medium text-[var(--color-text-1)]">
          {{ cell(record.order_mid) }}
        </div>
        <div class="text-[var(--color-text-2)]">
          OID: {{ cell(record.order_oid) }} · 出發:
          {{ fmtDt(record.go_date, true) || '—' }}
        </div>
        <!-- 訂單目前狀態（conversations 專屬）/ 金額 / 建立來源（product_reviews 亦有欄；
             其餘來源皆無值，恆不顯示）-->
        <div
          v-if="record.order_status_now || record.order_price || record.order_create_source_code"
          class="text-[var(--color-text-2)]"
        >
          <template v-if="record.order_status_now">狀態: {{ record.order_status_now }} · </template>
          金額: {{ cell(record.order_price) }} · 平台: {{ cell(record.order_create_source_code) }}
        </div>
      </div>
    </div>
    <!-- 商品 -->
    <div v-if="hasSection('product')" class="flex gap-1.5">
      <span v-if="showLabels" :class="SECTION_LABEL_CLASS">商品</span>
      <div class="min-w-0">
        <div v-if="record.prod_name" class="font-medium text-[var(--color-text-1)]">
          {{ record.prod_name }}
        </div>
        <span v-else class="text-gray-300">—</span>
        <div class="text-[var(--color-text-2)]">
          OID: {{ cell(record.prod_oid) }} · 語系: {{ cell(record.lang) }}
        </div>
      </div>
    </div>
    <!-- 方案 -->
    <div v-if="hasSection('package')" class="flex gap-1.5">
      <span v-if="showLabels" :class="SECTION_LABEL_CLASS">方案</span>
      <div class="min-w-0">
        <div v-if="record.package_name" class="text-[var(--color-text-1)]">
          {{ record.package_name }}
        </div>
        <span v-else class="text-gray-300">—</span>
        <div class="text-[var(--color-text-2)]">OID: {{ cell(record.pkg_oid) }}</div>
      </div>
    </div>
    <!-- 供應商（conversations 有名稱，優先顯示；OID 附註）-->
    <div v-if="hasSection('supplier')" class="flex gap-1.5">
      <span v-if="showLabels" :class="SECTION_LABEL_CLASS">供應商</span>
      <div class="min-w-0">
        <div v-if="record.supplier_name" class="font-medium text-[var(--color-text-1)]">
          {{ record.supplier_name }}
        </div>
        <div class="text-[var(--color-text-2)]">OID: {{ cell(record.supplier_oid) }}</div>
      </div>
    </div>
    <!-- 進線屬性（conversations 專屬：分桶/行程階段/處理方/客服標籤/訊息數，其餘來源恆空不顯示）-->
    <div v-if="hasSection('inbound')" class="flex gap-1.5">
      <span v-if="showLabels" :class="SECTION_LABEL_CLASS">進線</span>
      <div class="flex min-w-0 flex-col gap-1">
        <div v-if="record.bucket || record.trip_stage" class="flex flex-wrap items-center gap-1.5">
          <a-tag
            v-if="record.bucket"
            size="small"
            :color="BUCKET_COLORS[String(record.bucket)] || 'gray'"
          >
            {{ BUCKET_LABELS[String(record.bucket)] || record.bucket }}
          </a-tag>
          <a-tag v-if="record.trip_stage" size="small" color="arcoblue">
            {{ INBOUND_TRIP_STAGE_LABELS[String(record.trip_stage)] || record.trip_stage }}
          </a-tag>
        </div>
        <div
          v-if="record.msg_handler_bucket || record.cs_tag_name || record.user_message_count"
          class="text-[var(--color-text-2)]"
        >
          <template v-if="record.msg_handler_bucket"
            >處理方:
            {{
              MSG_HANDLER_BUCKET_LABELS[String(record.msg_handler_bucket)] ||
              record.msg_handler_bucket
            }}</template
          >
          <template v-if="record.cs_tag_name">
            <template v-if="record.msg_handler_bucket"> · </template
            >客服標籤: {{ record.cs_tag_name }}</template
          >
          <template v-if="record.user_message_count">
            <template v-if="record.msg_handler_bucket || record.cs_tag_name"> · </template>訊息數:
            {{ record.user_message_count }}</template
          >
        </div>
        <span
          v-if="
            !record.bucket &&
            !record.trip_stage &&
            !record.msg_handler_bucket &&
            !record.cs_tag_name &&
            !record.user_message_count
          "
          class="text-gray-300"
          >—</span
        >
      </div>
    </div>
    <!-- 組織分工：垂直分類／BD TAG／PM（bd_tag_vertical 系統，product_reviews 與 conversations 皆有值，
         獨立於上方「進線」段之外的共用段落） -->
    <div v-if="hasSection('org')" class="flex gap-1.5">
      <span v-if="showLabels" :class="SECTION_LABEL_CLASS">組織分工</span>
      <div class="flex min-w-0 flex-col gap-1">
        <div v-if="record.vertical" class="flex flex-wrap items-center gap-1.5">
          <a-tag size="small" color="cyan">{{ record.vertical }}</a-tag>
        </div>
        <div v-if="record.bd_tag" class="text-[var(--color-text-2)]">
          BD TAG: {{ record.bd_tag
          }}<template v-if="record.bd_tag_note"> · {{ record.bd_tag_note }}</template>
        </div>
        <div v-if="record.PM" class="text-[var(--color-text-2)]">PM: {{ record.PM }}</div>
        <span v-if="!record.vertical && !record.bd_tag && !record.PM" class="text-gray-300">—</span>
      </div>
    </div>
    <!-- 旅客 -->
    <div v-if="hasSection('traveller')" class="flex gap-1.5">
      <span v-if="showLabels" :class="SECTION_LABEL_CLASS">旅客</span>
      <div class="flex min-w-0 flex-wrap items-center gap-1.5">
        <a-tag v-if="record.traveller_type" size="small" color="arcoblue">
          {{ TRAVELLER_TYPE_LABELS[String(record.traveller_type)] || record.traveller_type }}
        </a-tag>
        <span v-if="record.member_uuid" class="break-all text-[var(--color-text-2)]">
          會員: {{ record.member_uuid }}
        </span>
      </div>
    </div>
  </div>
</template>

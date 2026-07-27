<script setup lang="ts">
/**
 * 歸因詳情抽屜（原 AttributionList 內 modal 抽出）：完整展示單一反饋的
 * 原文 → 關聯資料 → 每條歸因全欄位（分類路徑/信心含原始值/階段/判決狀態/摘要多語系/
 * 逐字佐證/建議行動/負責單位/真值/finding_id）。純展示、資料取自列上 attributions，零額外請求；
 * 全部走 Arco 現成組件（a-drawer / a-descriptions / a-tag / a-rate / a-typography）。
 */
import { computed, watch } from 'vue';
import { IconRefresh } from '@arco-design/web-vue/es/icon';
import { AsyncSection, JsonEditor } from '@/components';
import {
  ACTION_LABEL,
  BUCKET_COLORS,
  BUCKET_LABELS,
  DIALOGUE_ROLE_COLORS,
  DIALOGUE_ROLE_LABELS,
  DIALOGUE_SEGMENT_LABELS,
  EVIDENCE_EMPTY_TEXT,
  EVIDENCE_STATUS_COLOR,
  EVIDENCE_STATUS_LABEL,
  INBOUND_TRIP_STAGE_LABELS,
  MSG_HANDLER_BUCKET_LABELS,
  POLARITY_COLOR,
  POLARITY_LABELS,
  STAGE_LABELS,
  STATUS_COLOR,
  STATUS_LABEL,
  TIER_LABELS,
  TRAVELLER_TYPE_LABELS,
  type Attribution,
  type ProblemRow,
} from '../constants';
import { useOrderEvidence } from '../composables';
import { fmtDt, parseDialogue, type DialogueTurn } from '../utils';

const visible = defineModel<boolean>('visible', { default: false });
const props = defineProps<{ row: ProblemRow | null }>();

// 訂單佐證 lazy fetch：抽屜開啟且有 order_oid 才打（後端帶快取，重開便宜）
const { loading: evLoading, error: evError, result: evResult, load: evLoad } = useOrderEvidence();
watch(
  [visible, () => props.row?.order_oid],
  ([v, oid]) => {
    if (v && oid) void evLoad(String(oid));
  },
  { immediate: true },
);

/** 初判階段語義色（同列表：已初判綠 / 待複審橙 / 待數據補充藍）。 */
const STAGE_COLOR: Record<string, string> = {
  judged: 'green',
  pending_review: 'orange',
  pending_data: 'arcoblue',
};

/** 信心分層語義色（auto_accept 可採信綠 / jury 需複審橙 / needs_review 必人工紅）。 */
const TIER_COLOR: Record<string, string> = {
  auto_accept: 'green',
  jury: 'orange',
  needs_review: 'red',
};

/** 歸因路徑「L1 › L2」；未歸因回占位文字。 */
const attrPath = (a: Attribution): string =>
  [a.l1?.label, a.l2?.label].filter(Boolean).join(' › ') || '未歸因';

/** 最深層 code（L2 → L1 取第一個非空），路徑旁小字輔助定位規則樹。 */
const attrCode = (a: Attribution): string => a.l2?.code || a.l1?.code || '';

/** 欄位缺值顯示（'—'）。 */
const cell = (v: unknown): string => (v === null || v === undefined || v === '' ? '—' : String(v));

/** summary_langs 中「非 zh-tw」的其他語系（原文語言摘要，zh-tw 已是主顯示）。 */
const otherLangs = (a: Attribution): [string, string][] =>
  Object.entries(a.content?.summary_langs || {}).filter(([lang]) => lang !== 'zh-tw');

/** 反饋原文對話輪次：conversations 的 conversation_full 可解析出 [ROLE]: 前綴時回輪次陣列；
 * 其餘來源（無角色前綴）回 null，fallback 原樣全文顯示。 */
const dialogueTurns = computed<DialogueTurn[] | null>(() =>
  parseDialogue(String(props.row?.content || '')),
);
/** 該輪是否為新段落起點（首輪或與前一輪段落不同）：機器人／真人客服階段切換時插入分隔標籤。 */
const isNewSegment = (turns: DialogueTurn[], idx: number): boolean =>
  idx === 0 || turns[idx - 1].segment !== turns[idx].segment;
</script>

<template>
  <a-drawer
    v-model:visible="visible"
    :width="640"
    :footer="false"
    unmount-on-close
    :title="`歸因詳情 · #${row?.source_record_id ?? row?.source_id ?? ''}`"
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
        <template v-if="dialogueTurns">
          <div class="flex flex-col gap-1">
            <template v-for="(t, ti) in dialogueTurns" :key="ti">
              <!-- 段落分隔：機器人／真人客服階段切換時插入標籤（對齊 conversation_full 的 ‖ 分段）-->
              <div
                v-if="t.segment && isNewSegment(dialogueTurns, ti)"
                class="mt-1 text-[10px] font-semibold text-[var(--color-text-3)]"
              >
                {{ DIALOGUE_SEGMENT_LABELS[t.segment] || t.segment }}
              </div>
              <div class="text-sm leading-relaxed">
                <a-tag
                  v-if="t.role"
                  size="small"
                  :color="DIALOGUE_ROLE_COLORS[t.role] || 'gray'"
                  class="mr-1"
                  >{{ DIALOGUE_ROLE_LABELS[t.role] || t.role }}</a-tag
                >
                <span class="whitespace-pre-wrap text-[var(--color-text-1)]">{{ t.text }}</span>
              </div>
            </template>
          </div>
        </template>
        <div v-else class="whitespace-pre-wrap text-sm leading-relaxed text-[var(--color-text-1)]">
          {{ row.content || '（無內容）' }}
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
        <!-- 進線屬性（conversations 專屬：分桶/行程階段/處理方/出發日差，其餘來源無此欄恆顯示「—」）-->
        <a-descriptions-item
          v-if="row.bucket || row.trip_stage || row.msg_handler_bucket || row.godate_diff"
          label="進線屬性"
        >
          <div class="flex flex-wrap items-center gap-1.5">
            <a-tag v-if="row.bucket" size="small" :color="BUCKET_COLORS[String(row.bucket)] || 'gray'">
              {{ BUCKET_LABELS[String(row.bucket)] || row.bucket }}
            </a-tag>
            <a-tag v-if="row.trip_stage" size="small" color="arcoblue">
              {{ INBOUND_TRIP_STAGE_LABELS[String(row.trip_stage)] || row.trip_stage }}
            </a-tag>
            <span v-if="row.msg_handler_bucket" class="text-xs text-[var(--color-text-2)]">
              處理方
              {{
                MSG_HANDLER_BUCKET_LABELS[String(row.msg_handler_bucket)] || row.msg_handler_bucket
              }}
            </span>
            <span v-if="row.godate_diff" class="text-xs text-[var(--color-text-2)]">
              出發日差 {{ row.godate_diff }}
            </span>
          </div>
        </a-descriptions-item>
        <a-descriptions-item label="訂單">
          <div class="font-medium">{{ cell(row.order_mid) }}</div>
          <div class="text-xs text-[var(--color-text-2)]">
            OID {{ cell(row.order_oid) }} · 出發
            {{ fmtDt(String(row.go_date ?? ''), true) || '—' }}
          </div>
          <!-- 進線專屬：訂單狀態/金額/利潤/語系/建立來源與時間（其餘來源恆空不顯示）-->
          <div
            v-if="row.order_status_now || row.order_price || row.order_profit"
            class="text-xs text-[var(--color-text-2)]"
          >
            {{ cell(row.order_status_now) }} · {{ cell(row.order_price) }} · 利潤
            {{ cell(row.order_profit) }}
          </div>
          <div
            v-if="row.order_lang || row.order_create_source_code || row.order_create_time"
            class="text-xs text-[var(--color-text-2)]"
          >
            語系 {{ cell(row.order_lang) }} · 建立來源 {{ cell(row.order_create_source_code) }} ·
            建立時間 {{ fmtDt(String(row.order_create_time ?? '')) || '—' }}
          </div>
        </a-descriptions-item>
        <a-descriptions-item label="商品">
          <div v-if="row.prod_name" class="font-medium">
            {{ row.prod_name }}
          </div>
          <div class="text-xs text-[var(--color-text-2)]">
            OID {{ cell(row.prod_oid) }} · {{ cell(row.product_category_main) }} ·
            {{ cell(row.lang) }}
          </div>
          <!-- 進線專屬：商品時區/垂直分類/BD 標籤/PM（其餘來源恆空不顯示）-->
          <div
            v-if="row.product_tz || row.vertical"
            class="text-xs text-[var(--color-text-2)]"
          >
            時區 {{ cell(row.product_tz) }} · 垂直分類 {{ cell(row.vertical) }}
          </div>
          <div v-if="row.bd_tag || row.bd_tag_cd || row.PM" class="text-xs text-[var(--color-text-2)]">
            BD {{ cell(row.bd_tag) }}（{{ cell(row.bd_tag_cd) }}） · PM {{ cell(row.PM) }}
          </div>
        </a-descriptions-item>
        <a-descriptions-item label="方案">
          <div v-if="row.package_name">{{ row.package_name }}</div>
          <div class="text-xs text-[var(--color-text-2)]">OID {{ cell(row.pkg_oid) }}</div>
        </a-descriptions-item>
        <a-descriptions-item label="供應商">
          <div v-if="row.supplier_name" class="font-medium">{{ row.supplier_name }}</div>
          <div class="text-xs text-[var(--color-text-2)]">OID {{ cell(row.supplier_oid) }}</div>
        </a-descriptions-item>
        <!-- 客服標籤（conversations 專屬；其餘來源恆空不顯示）-->
        <a-descriptions-item
          v-if="row.cs_tag_name || row.cs_tag_oid || row.user_message_count"
          label="客服標籤"
        >
          <span v-if="row.cs_tag_name">{{ row.cs_tag_name }}</span>
          <span v-if="row.cs_tag_oid" class="ml-1.5 text-xs text-[var(--color-text-2)]"
            >({{ row.cs_tag_oid }})</span
          >
          <span v-if="row.user_message_count" class="ml-1.5 text-xs text-[var(--color-text-2)]">
            訊息數 {{ row.user_message_count }}
          </span>
        </a-descriptions-item>
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

      <!-- ②b 訂單佐證（production 下單當時商品快照·lazy fetch·三態）-->
      <div v-if="row.order_oid">
        <div class="mb-2 flex items-center gap-2">
          <span class="text-base font-medium text-[var(--color-text-1)]">訂單佐證</span>
          <a-tag
            v-if="evResult?.status"
            size="small"
            :color="EVIDENCE_STATUS_COLOR[evResult.status]"
          >
            {{ EVIDENCE_STATUS_LABEL[evResult.status] || evResult.status }}
          </a-tag>
          <a-button
            size="mini"
            type="text"
            :disabled="evLoading"
            @click="evLoad(String(row.order_oid), true)"
          >
            <template #icon><icon-refresh /></template>
          </a-button>
        </div>
        <AsyncSection
          :loading="evLoading"
          :error="evError"
          :empty="!!evResult && !evResult.data"
          :empty-text="evResult ? EVIDENCE_EMPTY_TEXT[evResult.status] || evResult.status : ''"
          :skeleton-rows="4"
        >
          <JsonEditor
            v-if="evResult?.data"
            :json="evResult.data"
            read-only
            mode="tree"
            auto-height
          />
        </AsyncSection>
      </div>

      <!-- ③ 每條歸因：全欄位 descriptions（標題列帶主歸因/判決狀態/真值徽章）-->
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
            <!-- 校準後 value ≠ LLM 原始 raw 時並列原始值，供人工判決判讀校準幅度 -->
            <span
              v-if="
                typeof a.confidence?.raw === 'number' && a.confidence.raw !== a.confidence.value
              "
              class="ml-1.5 text-xs text-[var(--color-text-3)]"
            >
              原始 {{ a.confidence.raw.toFixed(2) }}
            </span>
          </a-descriptions-item>
          <a-descriptions-item label="初判階段">
            <a-tag v-if="a.stage" size="small" :color="STAGE_COLOR[a.stage]">
              {{ STAGE_LABELS[a.stage] || a.stage }}
            </a-tag>
            <span v-else>—</span>
          </a-descriptions-item>
          <a-descriptions-item label="初判模型">
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
      <a-empty v-else description="此列尚無歸因（未初判 / 正向不歸因）" />
    </div>
  </a-drawer>
</template>

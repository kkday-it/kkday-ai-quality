<script setup lang="ts">
/**
 * 歸因詳情抽屜（原 AttributionList 內 modal 抽出）：完整展示單一反饋的
 * 原文 → 關聯資料 → 每條歸因全欄位（分類路徑/信心含原始值/階段/摘要多語系/
 * 逐字佐證/建議行動/負責單位/真值/歸因流水號）。純展示、資料取自列上 attributions，零額外請求；
 * 全部走 Arco 現成組件（a-drawer / a-descriptions / a-tag / a-rate / a-typography）。
 */
import { computed, watch } from 'vue';
import { IconRefresh } from '@arco-design/web-vue/es/icon';
import { AsyncSection, JsonEditor } from '@/components';
import ExternalReviewPanel from './ExternalReviewPanel.vue';
import RecordContextPanel from './RecordContextPanel.vue';
import {
  ACTION_LABEL,
  DIALOGUE_ROLE_COLORS,
  DIALOGUE_ROLE_LABELS,
  DIALOGUE_SEGMENT_LABELS,
  EVIDENCE_EMPTY_TEXT,
  EVIDENCE_STATUS_COLOR,
  EVIDENCE_STATUS_LABEL,
  POLARITY_COLOR,
  POLARITY_LABELS,
  schemaFor,
  STAGE_LABELS,
  TIER_LABELS,
  type Attribution,
  type ProblemRow,
} from '../constants';
import { useOrderEvidence } from '../composables';
import { fmtDt, parseDialogue, sentimentClass, type DialogueTurn } from '../utils';

const visible = defineModel<boolean>('visible', { default: false });
const props = defineProps<{
  row: ProblemRow | null;
  /** 反饋來源 code：決定「補充」/「關聯資料」的段落歸屬（與列表共用 `schemaFor` 同一份 schema）。 */
  source: string;
}>();

/** 「補充」區塊段落（該反饋自身的附加屬性，如進線的分桶/行程階段/處理方）。 */
const supplementSections = computed(() => schemaFor(props.source).supplementSections);
/** 「關聯資料」區塊段落（訂單/商品/方案等關聯實體）。 */
const contextSections = computed(() => schemaFor(props.source).contextSections);
/** 是否有外部評論融合資料（無則不渲染該區塊，避免空表）。 */
const hasExternal = computed(
  () => !!props.row?.ext_sentiment || !!(props.row?.ext_free_tag as unknown[] | undefined)?.length,
);

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
          <!-- 我方情緒分 1-5（重新初判後回填；與下方外部評論情緒分同尺度可直接對比）-->
          <span v-if="row.our_sentiment" class="flex items-center gap-1 text-xs">
            <span class="text-[var(--color-text-3)]">情緒分:</span>
            <span class="font-semibold" :class="sentimentClass(row.our_sentiment)">
              {{ row.our_sentiment }}/5
            </span>
          </span>
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

      <!-- ② 補充：關於這則反饋自身的附加屬性——進線屬性/客服標籤（supplementSections）+ 外部評論
           融合維度。與列表「補充」區塊同一份歸屬（schemaFor），僅版型不同（此處完整鋪開）。 -->
      <RecordContextPanel
        v-if="supplementSections.length"
        :record="row"
        variant="detail"
        title="補充"
        :sections="supplementSections"
      />
      <ExternalReviewPanel v-if="hasExternal" :record="row" variant="detail" />

      <!-- ③ 關聯資料：訂單/商品/方案等關聯實體，收斂為共用元件 RecordContextPanel
           （detail 版型＝descriptions 完整鋪開，含列表放不下的利潤／語系／建立時間／商品時區
           ／BD TAG code；欄位歸屬與列表共用同一份真相源）。 -->
      <RecordContextPanel :record="row" variant="detail" :sections="contextSections" />

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

      <!-- ③ 每條歸因：全欄位 descriptions（標題列帶主歸因徽章）-->
      <template v-if="row.attributions && row.attributions.length">
        <a-descriptions
          v-for="(a, ai) in row.attributions"
          :key="a.attribution_oid ?? ai"
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
          <a-descriptions-item label="歸因 ID">
            <span class="break-all text-xs text-[var(--color-text-3)]">{{
              a.attribution_oid ?? '—'
            }}</span>
          </a-descriptions-item>
        </a-descriptions>
      </template>
      <a-empty v-else description="此列尚無歸因（未初判 / 正向不歸因）" />
    </div>
  </a-drawer>
</template>

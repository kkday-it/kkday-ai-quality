<script setup lang="ts">
/**
 * 歸因列表單條「測試」彈窗（Prompt-as-Source 調適閉環）：對這一則評論即時跑 prompts → 分類結果,
 * 與現有判決並排比對。**不落庫**（dry-run 預覽「改 prompt 後這條會怎麼判」,不覆寫現有判決）。
 * 與列級「初判分類」（重判並覆寫落庫）區隔。下方「六域裁決」為診斷理由 overlay（B0）：無論
 * 該域是否命中都附一句話理由，供調適時定位「邊界寫糊」或「例句缺」。
 */
import { computed, ref, watch } from 'vue';
import { Message } from '@arco-design/web-vue';
import { classifyOne, type ClassifyOneResult } from '@/api/judgment.api';
import type { Attribution, ProblemRow } from '../constants/source-schema.constant';

const props = defineProps<{
  /** 是否顯示。 */
  visible: boolean;
  /** 當前反饋來源 code（product_reviews…）。 */
  source: string;
  /** 目標列（帶 source_id / content / attributions）。 */
  row: ProblemRow | null;
}>();

const emit = defineEmits<{ (e: 'update:visible', v: boolean): void }>();

const loading = ref(false);
const result = ref<ClassifyOneResult | null>(null);

/** 傾向碼→中文 label。 */
const POL: Record<string, string> = { negative: '負向', neutral: '中立', positive: '正向' };
const polLabel = (p?: string): string => POL[p ?? ''] || p || '—';

/** 評論原文：優先本次測試回傳（權威）,否則列上動態 content/title。 */
const reviewText = computed(
  () => result.value?.text || String(props.row?.content ?? props.row?.title ?? ''),
);
/** 現有判決（列上 attributions；唯讀）。 */
const existing = computed<Attribution[]>(() => props.row?.attributions ?? []);

async function run() {
  if (!props.row?.source_id) return;
  loading.value = true;
  result.value = null;
  try {
    result.value = await classifyOne(props.source, props.row.source_id);
  } catch (e) {
    Message.error(e instanceof Error ? e.message : '測試失敗');
  } finally {
    loading.value = false;
  }
}

// 關閉清結果（下次開重測，避免看到上一列殘留）
watch(
  () => props.visible,
  (v) => {
    if (!v) result.value = null;
  },
);
</script>

<template>
  <a-modal
    :visible="visible"
    title="測試分類（對這一則跑 prompts · 不落庫）"
    :width="820"
    :footer="false"
    @cancel="emit('update:visible', false)"
  >
    <!-- 評論原文 -->
    <div class="mb-3 rounded-lg border bg-[var(--color-fill-1)] p-3">
      <div class="mb-1 text-xs font-medium text-[var(--color-text-3)]">評論原文</div>
      <div class="max-h-32 overflow-auto whitespace-pre-wrap text-sm leading-relaxed">
        {{ reviewText || '（無文字）' }}
      </div>
    </div>

    <div class="mb-3 flex items-center gap-3">
      <span class="text-xs text-[var(--color-text-3)]">對這一則即時跑 prompts,不覆寫現有判決</span>
      <div class="flex-1" />
      <a-button type="primary" size="small" :loading="loading" @click="run">執行測試</a-button>
    </div>

    <!-- 現有判決 vs 本次測試 並排 -->
    <div class="grid grid-cols-2 gap-3">
      <!-- 現有判決（列上，唯讀） -->
      <div class="rounded-lg border p-3">
        <div class="mb-2 flex items-center gap-2">
          <span class="text-sm font-medium">現有判決</span>
          <a-tag size="small">{{ polLabel(row?.polarity) }}</a-tag>
        </div>
        <div v-if="existing.length" class="flex flex-col gap-2">
          <div
            v-for="(a, i) in existing"
            :key="a.finding_id || i"
            class="rounded border-l-2 border-[var(--color-border-3)] bg-[var(--color-fill-1)] px-2 py-1.5"
          >
            <div class="flex items-center gap-1.5 text-sm">
              <a-tag v-if="a.is_primary" size="small" color="arcoblue">主</a-tag>
              <span class="font-medium">{{ a.l1?.label }}</span>
              <span class="text-[var(--color-text-3)]">›</span>
              <span>{{ a.l2?.label }}</span>
              <span class="ml-auto font-mono text-xs text-[var(--color-text-3)]"
                >{{ Math.round((a.confidence?.value ?? 0) * 100) }}%</span
              >
            </div>
            <div v-if="a.content?.summary" class="mt-0.5 text-xs text-[var(--color-text-2)]">
              {{ a.content.summary }}
            </div>
          </div>
        </div>
        <a-empty v-else description="未歸因" :image-size="40" />
      </div>

      <!-- 本次測試（即時 prompts） -->
      <div class="rounded-lg border p-3">
        <div class="mb-2 flex items-center gap-2">
          <span class="text-sm font-medium">本次測試</span>
          <a-tag v-if="result" size="small" color="green">{{ polLabel(result.polarity) }}</a-tag>
          <span v-if="result" class="text-[11px] text-[var(--color-text-3)]"
            >· {{ result.model }}</span
          >
        </div>
        <a-spin v-if="loading" class="block py-6 text-center" />
        <template v-else-if="result">
          <div v-if="result.attributions.length" class="flex flex-col gap-2">
            <div
              v-for="(a, i) in result.attributions"
              :key="i"
              class="rounded border-l-2 border-[rgb(var(--green-4))] bg-[rgba(var(--green-1),0.5)] px-2 py-1.5"
            >
              <div class="flex items-center gap-1.5 text-sm">
                <a-tag v-if="a.is_primary" size="small" color="green">主</a-tag>
                <span class="font-medium">{{ a.l1_label }}</span>
                <span class="text-[var(--color-text-3)]">›</span>
                <span>{{ a.l2_label }}</span>
                <span class="ml-auto font-mono text-xs text-[var(--color-text-3)]"
                  >{{ Math.round(a.confidence * 100) }}%</span
                >
              </div>
              <div
                v-if="Object.values(a.summary || {})[0]"
                class="mt-0.5 text-xs text-[var(--color-text-2)]"
              >
                {{ Object.values(a.summary)[0] }}
              </div>
              <div v-if="a.evidence_quote" class="mt-0.5 text-[11px] text-[var(--color-text-3)]">
                佐證：{{ a.evidence_quote }}
              </div>
            </div>
          </div>
          <a-empty v-else description="本次測試無歸因（非問題）" :image-size="40" />
        </template>
        <div v-else class="py-6 text-center text-xs text-[var(--color-text-3)]">
          點「執行測試」跑 prompts
        </div>
      </div>
    </div>

    <!-- 六域裁決（診斷理由 overlay）：無論命中與否，六個域都有交代 -->
    <a-collapse v-if="result && result.domain_verdicts.length" class="mt-3" :bordered="false">
      <a-collapse-item key="verdicts" header="六域裁決（診斷理由）">
        <div class="flex flex-col gap-1.5">
          <div
            v-for="v in result.domain_verdicts"
            :key="v.domain"
            class="rounded border px-2 py-1.5 text-xs"
          >
            <div class="flex items-center gap-1.5">
              <a-tag size="small" :color="v.matched ? 'green' : 'gray'">{{
                v.matched ? '✅ 命中' : '⭕ 棄權'
              }}</a-tag>
              <span class="font-medium">{{ v.domain_label }}</span>
              <template v-if="v.matched">
                <span class="text-[var(--color-text-3)]">›</span>
                <span>{{ v.attributions[0]?.l2_label }}</span>
              </template>
            </div>
            <div class="mt-1 text-[var(--color-text-2)]">
              {{ v.matched ? v.attributions[0]?.reason : v.abstain_reason }}
            </div>
          </div>
        </div>
      </a-collapse-item>
    </a-collapse>
  </a-modal>
</template>

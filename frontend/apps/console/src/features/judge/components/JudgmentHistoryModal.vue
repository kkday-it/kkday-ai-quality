<script setup lang="ts">
/**
 * 判決歷史彈窗（評論級時間軸）：某則評論 (source, source_id) 的歷次判決快照 / 覆核轉移 / 備註
 * 三類事件混排（新到舊，Arco a-timeline）。判決事件附「與前一次判決的變更」徽章
 * （模型/歸因數/分類/內容——client-side 對比，無需後端 diff 端點）；右側可新增評論級備註
 * （與 finding 級「歸因備註」並存，兩個入口不同層級）。
 *
 * 資料源＝GET /api/judgment-history（append-only judgment_history 表；重判結果與前次完全
 * 相同時後端去重不落新列，時間軸只呈現真正的變化）。
 */
import { computed, ref, watch } from 'vue';
import { Message } from '@arco-design/web-vue';
import { addJudgmentHistoryNote, getJudgmentHistory, type JudgmentHistoryEntry } from '@/api';
import { StateGuard } from '@/components';
import { POLARITY_LABELS, STATUS_LABEL, type ProblemRow } from '../constants';

const props = defineProps<{
  visible: boolean;
  /** 反饋來源 code（product_reviews…）。 */
  source: string;
  /** 目標評論列（取 _group＝source_id；null＝未選）。 */
  row: ProblemRow | null;
}>();
const emit = defineEmits<{ 'update:visible': [v: boolean] }>();

const open = computed({
  get: () => props.visible,
  set: (v: boolean) => emit('update:visible', v),
});
const sourceId = computed(() => String(props.row?._group ?? ''));

const list = ref<JudgmentHistoryEntry[]>([]);
const loading = ref(false);
const draft = ref('');
const saving = ref(false);

/** 載入時間軸（開窗時觸發；失敗顯示錯誤 toast、清空列表）。 */
const load = async () => {
  if (!sourceId.value) return;
  loading.value = true;
  try {
    list.value = await getJudgmentHistory(props.source, sourceId.value);
  } catch (e: any) {
    list.value = [];
    Message.error('載入判決歷史失敗：' + (e?.message || e));
  } finally {
    loading.value = false;
  }
};
watch(
  () => props.visible,
  (v) => {
    if (v) {
      draft.value = '';
      list.value = [];
      void load();
    }
  },
);

/** 送出評論級備註，成功後置頂插入時間軸。 */
const submitNote = async () => {
  const content = draft.value.trim();
  if (!content) return;
  saving.value = true;
  try {
    const created = await addJudgmentHistoryNote(props.source, sourceId.value, content);
    list.value = [created, ...list.value];
    draft.value = '';
    Message.success('已新增備註');
  } catch (e: any) {
    Message.error('新增備註失敗：' + (e?.message || e));
  } finally {
    saving.value = false;
  }
};

/** 時間顯示（ISO → 'YYYY-MM-DD HH:mm:ss'；與備註彈窗 fmtNoteTime 同格式）。 */
const fmtTime = (iso: string | null): string => (iso ? iso.replace('T', ' ').slice(0, 19) : '');

/** 事件類型 → timeline 節點色（judgment 藍＝AI 判決 / status 橙＝人工覆核 / note 灰＝備註）。 */
const DOT_COLOR: Record<string, string> = {
  judgment: 'rgb(var(--primary-6))',
  status: 'rgb(var(--warning-6))',
  note: 'var(--color-neutral-6)',
};

/** 判決快照單筆（後端 snapshot_of 形狀；寬鬆型別容忍回填/新版欄位差異）。 */
type Snap = {
  finding_id?: string;
  polarity?: string;
  sentiment_score?: number | null;
  l1?: { code?: string; label?: string };
  l2?: { code?: string; label?: string };
  l3?: { code?: string; label?: string };
  confidence?: { value?: number | null; tier?: string };
  content?: { summary?: unknown };
  is_primary?: boolean;
};

const snapsOf = (e: JudgmentHistoryEntry): Snap[] => (e.attributions as Snap[] | null) ?? [];

/** 快照摘要文字：summary 為語系 map（取 zh-tw；回退首值）或純字串。 */
const snapSummary = (s: Snap): string => {
  const raw = s.content?.summary;
  if (typeof raw === 'string') return raw;
  if (raw && typeof raw === 'object') {
    const m = raw as Record<string, string>;
    return m['zh-tw'] || Object.values(m)[0] || '';
  }
  return '';
};

/** L1›L2›L3 麵包屑（缺層自動略過）。 */
const snapPath = (s: Snap): string =>
  [s.l1?.label, s.l2?.label, s.l3?.label].filter(Boolean).join(' › ') || '未歸因';

/** 快照結構鍵（分類變化對比用：傾向+情緒分+L1-L3 code，排序後串接）。 */
const structKey = (snaps: Snap[]): string =>
  snaps
    .map(
      (s) =>
        `${s.polarity}|${s.sentiment_score}|${s.l1?.code || ''}|${s.l2?.code || ''}|${s.l3?.code || ''}`,
    )
    .sort()
    .join(';');

/**
 * 判決事件 vs 前一次判決的變更徽章（oldest→newest 逐筆對比；首筆回「初次判決」）。
 * 後端去重保證相鄰判決必有差異：模型/歸因數/分類 逐項報，僅措辭信心漂移歸「內容微調」。
 */
const changesById = computed<Record<number, string[]>>(() => {
  const judgments = [...list.value].filter((e) => e.kind === 'judgment').reverse(); // oldest→newest
  const out: Record<number, string[]> = {};
  judgments.forEach((e, i) => {
    if (i === 0) {
      out[e.id] = [(e.params as any)?.backfilled ? '初始回填' : '初次判決'];
      return;
    }
    const prev = judgments[i - 1];
    const tags: string[] = [];
    if ((prev.model || '') !== (e.model || '')) tags.push(`模型 ${prev.model || '—'}→${e.model}`);
    const pn = snapsOf(prev).length;
    const n = snapsOf(e).length;
    if (pn !== n) tags.push(`歸因數 ${pn}→${n}`);
    if (structKey(snapsOf(prev)) !== structKey(snapsOf(e))) tags.push('分類變化');
    else if (!tags.length) tags.push('內容微調');
    out[e.id] = tags;
  });
  return out;
});

/** 覆核轉移事件文案：目標狀態 + 各 finding 原狀態（params={to, changes:[{finding_id, from}]}）。 */
const statusText = (e: JudgmentHistoryEntry): string => {
  const p = (e.params ?? {}) as { to?: string; changes?: { from?: string }[] };
  const to = STATUS_LABEL[p.to || ''] || p.to || '';
  const n = p.changes?.length ?? 0;
  const froms = [...new Set((p.changes ?? []).map((c) => STATUS_LABEL[c.from || ''] || c.from))];
  return `${n} 條歸因 ${froms.join('/')} → ${to}`;
};
</script>

<template>
  <a-modal v-model:visible="open" title="判決歷史" :footer="false" :width="860" unmount-on-close>
    <div class="flex gap-5">
      <!-- 左：評論級事件時間軸（判決快照 / 覆核轉移 / 備註，新到舊）-->
      <div class="min-w-0 flex-1">
        <StateGuard :loading="loading" error="">
          <a-timeline v-if="list.length" class="max-h-[440px] overflow-auto pl-1 pr-2">
            <a-timeline-item v-for="e in list" :key="e.id" :dot-color="DOT_COLOR[e.kind]">
              <!-- 首行：時間 + 事件身分 -->
              <div
                class="flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-[var(--color-text-3)]"
              >
                <span>{{ fmtTime(e.created_at) }}</span>
                <template v-if="e.kind === 'judgment'">
                  <a-tag size="small" color="purple">{{ e.model || '—' }}</a-tag>
                  <a-tag
                    v-for="c in changesById[e.id] || []"
                    :key="c"
                    size="small"
                    color="arcoblue"
                    bordered
                  >
                    {{ c }}
                  </a-tag>
                  <span v-if="e.triggered_by">by {{ e.triggered_by }}</span>
                </template>
                <template v-else-if="e.kind === 'status'">
                  <a-tag size="small" color="orange">人工覆核</a-tag>
                  <span class="font-medium text-[var(--color-text-2)]">{{ e.author }}</span>
                </template>
                <template v-else>
                  <a-tag size="small" color="gray">備註</a-tag>
                  <span class="font-medium text-[var(--color-text-2)]">{{ e.author }}</span>
                </template>
              </div>
              <!-- 內容：依事件類型 -->
              <div v-if="e.kind === 'judgment'" class="mt-1 flex flex-col gap-1">
                <div
                  v-for="(s, si) in snapsOf(e)"
                  :key="si"
                  class="rounded bg-[var(--color-fill-1)] px-2 py-1 text-xs leading-snug"
                >
                  <div class="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                    <span class="font-medium text-[rgb(var(--primary-6))]">{{ snapPath(s) }}</span>
                    <span class="text-[var(--color-text-3)]">
                      {{ POLARITY_LABELS[s.polarity || ''] || s.polarity || '—'
                      }}<template v-if="s.sentiment_score">
                        · 情緒分 {{ s.sentiment_score }}/5</template
                      >
                      <template v-if="typeof s.confidence?.value === 'number'">
                        · 信心 {{ s.confidence.value.toFixed(2) }}</template
                      >
                    </span>
                  </div>
                  <div v-if="snapSummary(s)" class="mt-0.5 text-[var(--color-text-2)]">
                    {{ snapSummary(s) }}
                  </div>
                </div>
                <div
                  v-if="((e.params as any)?.voter_models || []).length"
                  class="text-[11px] text-[var(--color-text-3)]"
                >
                  ensemble voters：{{ ((e.params as any).voter_models as string[]).join('、') }}
                </div>
              </div>
              <div
                v-else-if="e.kind === 'status'"
                class="mt-0.5 text-xs text-[var(--color-text-1)]"
              >
                {{ statusText(e) }}
              </div>
              <div
                v-else
                class="mt-0.5 whitespace-pre-wrap text-xs leading-snug text-[var(--color-text-1)]"
              >
                {{ e.content }}
              </div>
            </a-timeline-item>
          </a-timeline>
          <a-empty v-else description="尚無判決歷史" />
        </StateGuard>
      </div>
      <!-- 右：新增評論級備註（固定寬；與 finding 級「歸因備註」並存）-->
      <div
        class="flex w-[260px] shrink-0 flex-col gap-2 border-l border-[var(--color-neutral-3)] pl-5"
      >
        <a-textarea
          v-model="draft"
          :auto-size="{ minRows: 4 }"
          :max-length="500"
          show-word-limit
          placeholder="輸入評論級備註（記錄本則評論的處理脈絡）…"
        />
        <div class="flex justify-end">
          <a-button
            type="primary"
            size="small"
            :loading="saving"
            :disabled="!draft.trim()"
            @click="submitNote"
          >
            送出備註
          </a-button>
        </div>
      </div>
    </div>
  </a-modal>
</template>

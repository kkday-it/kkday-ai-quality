<script setup lang="ts">
/**
 * 反饋時間軸抽屜：某則反饋 (source, source_id) 的所有事件按時間排在**同一條軸**上
 * （初判快照 / 初判失敗 / 人工糾正 / 複審確認 / 待審建議 / 備註），舊到新，Arco a-timeline。
 *
 * `scope` prop 切階段視圖（初判 / 判決 / 人工），切的是「看哪一段」而非開第二條軸——
 * 理由見 constants/timeline.constant.ts。初判事件附「與前一次初判的變更」徽章（模型/歸因數/
 * 分類/內容，client-side 對比無需後端 diff 端點）+「查看 LLM 日誌」入口（job_id 存在時，開
 * `PrejudgeLogDrawer` 回看當時完整快照）；人工段可新增備註。
 *
 * 備註是**反饋級**的、一則反饋只有一份。曾經另有 finding 級的「歸因備註」，已於 2026-08-04
 * 隨 `finding_notes` 表退役——它綁 attribution_oid，而歸因列每次重新初判都整批換掉，8 列裡
 * 6 列成了孤兒。
 *
 * 資料源＝GET /api/attribution-history（append-only attribution_history 表；重新初判結果與前次完全
 * 相同時後端去重不落新列，時間軸只呈現真正的變化）。
 */
import { computed, defineAsyncComponent, ref, watch } from 'vue';
import { Message } from '@arco-design/web-vue';
import { IconCode, IconSend } from '@arco-design/web-vue/es/icon';
import {
  addAttributionNote,
  getAttributionHistory,
  getNoteTypes,
  type AttributionHistoryEntry,
  type NoteType,
} from '@/api';
import { ScrollFadeArea, StateGuard } from '@/components';
import {
  CHANGE_TYPE_LABELS,
  FIELD_LABELS,
  POLARITY_LABELS,
  type ProblemRow,
  TIMELINE_SCOPE_EMPTY,
  TIMELINE_SCOPE_KINDS,
  TIMELINE_SCOPE_TITLE,
  type TimelineScope,
} from '../constants';
import { fmtTimelineTime, formatActor } from '../utils';

// 「查看 LLM 日誌」入口目標（點開才載；PrejudgeLogDrawer 為歷史快照回看專用）
const PrejudgeLogDrawer = defineAsyncComponent(() => import('./PrejudgeLogDrawer.vue'));
const logDrawerVisible = ref(false);
const logDrawerJobId = ref('');
const openRunLog = (jobId: string) => {
  logDrawerJobId.value = jobId;
  logDrawerVisible.value = true;
};

const props = defineProps<{
  visible: boolean;
  /** 反饋來源 code（reviews…）。 */
  source: string;
  /** 目標反饋列（取 _group＝source_id；null＝未選）。 */
  row: ProblemRow | null;
  /**
   * 階段視圖（預設 `all`）。**一則反饋只有一條時間軸**，這裡切的是「看哪一段」而非開第二條軸。
   *
   * 之所以不做成「初判歷史」「判決歷史」兩個各自獨立的抽屜：糾正、備註、待審建議這些事件同時
   * 跨初判與判決兩個階段，拆成兩條軸之後每加一種事件都要重新吵一次它該歸哪邊，而且使用者永遠
   * 看不到完整的先後順序。一條軸 + 階段過濾兩者都能給。
   */
  scope?: TimelineScope;
}>();
const emit = defineEmits<{ 'update:visible': [v: boolean] }>();

const open = computed({
  get: () => props.visible,
  set: (v: boolean) => emit('update:visible', v),
});
const sourceId = computed(() => String(props.row?._group ?? ''));

const list = ref<AttributionHistoryEntry[]>([]);
const loading = ref(false);
const draft = ref('');
const saving = ref(false);
/** 互動類型選項（值域主檔的 note_type 軸；業務可於設定 › 判決值域自行增減）。 */
const noteTypes = ref<NoteType[]>([]);
const draftType = ref('internal');

/** 載入時間軸（開窗時觸發；失敗顯示錯誤 toast、清空列表）。 */
const load = async () => {
  if (!sourceId.value) return;
  loading.value = true;
  try {
    list.value = await getAttributionHistory(props.source, sourceId.value);
  } catch (e: any) {
    list.value = [];
    Message.error('載入歸因歷史失敗：' + (e?.message || e));
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
      if (!noteTypes.value.length) {
        void getNoteTypes().then((t) => {
          noteTypes.value = t;
          if (t.length && !t.some((x) => x.item_code === draftType.value)) {
            draftType.value = t[0].item_code;
          }
        });
      }
    }
  },
);

/**
 * 送出備註（**append-only**：寫出去不能改也不能撤回）。
 *
 * 這裡送的是**整則備註**（不帶 slot）——面向備註的入口在糾正工作台的每一列，因為人是在看那條
 * 歸因時才最想留話。送出後重新載入整條時間軸，而不是把回傳值 push 進陣列：備註與事件來自兩張表、
 * 由後端合併排序，前端自己插入會排錯位置。
 */
const submitNote = async () => {
  const content = draft.value.trim();
  if (!content) return;
  saving.value = true;
  try {
    await addAttributionNote({
      source: props.source,
      source_id: sourceId.value,
      note_type: draftType.value,
      content,
    });
    draft.value = '';
    await load();
    Message.success('已新增備註');
  } catch (e: any) {
    Message.error('新增備註失敗：' + (e?.message || e));
  } finally {
    saving.value = false;
  }
};


/**
 * 事件類型 → timeline 節點色。
 *
 * ⚠️ key 必須是後端實際寫入的 kind 值（`prejudge`/`note`/`failure`）。曾經誤用早已改掉的舊名
 * `judgment`/`status`，結果整條時間軸的節點色都取到 undefined、靜默走預設灰。
 */
/** 人工介入事件（需要專屬渲染，不能落到備註兜底分支）。 */
const MANUAL_KINDS = ['correction', 'review_confirm', 'suggestion', 'suggestion_resolved'];

const scope = computed<TimelineScope>(() => props.scope ?? 'all');
const scopeTitle = computed(() => TIMELINE_SCOPE_TITLE[scope.value]);
const scopeEmpty = computed(() => TIMELINE_SCOPE_EMPTY[scope.value]);

/**
 * 當前階段視圖要顯示的事件。
 *
 * ⚠️ 只影響**渲染**：`list` 仍持有完整時間軸，因為快照 diff（見 `diffOf`）必須拿到所有
 * `prejudge` 事件才算得出前後差異——用過濾後的清單去 diff 會在階段視圖下算出錯誤的 delta。
 */
const visibleList = computed(() => {
  const kinds = TIMELINE_SCOPE_KINDS[scope.value];
  return kinds ? list.value.filter((e) => kinds.includes(e.kind)) : list.value;
});

/** 備註的互動類型顯示名（值域主檔的 label；查不到時退回機器碼，不假裝有翻譯）。 */
const noteTypeLabel = (e: AttributionHistoryEntry): string => {
  const code = String((e.params as { note_type?: string } | null)?.note_type ?? '');
  return noteTypes.value.find((t) => t.item_code === code)?.item_label || code || '備註';
};

/** 待跟進用橙色點出「這條還沒結案」，其餘中性灰——顏色只用來標未結案，不做全彩分類。 */
const noteTypeColor = (e: AttributionHistoryEntry): string =>
  (e.params as { note_type?: string } | null)?.note_type === 'follow_up' ? 'orange' : 'gray';

/** 備註槽位（後端已補上顯示名與當下狀態，見 `_annotate_slot`）。 */
interface NoteSlot {
  l1_code?: string;
  l2_code?: string;
  l1_label?: string;
  l2_label?: string;
  state?: 'live' | 'dismissed' | 'gone';
}
const slotOf = (e: AttributionHistoryEntry): NoteSlot | null =>
  (e.params as { slot?: NoteSlot } | null)?.slot ?? null;

/** 面向備註的所屬面向（整則備註回空字串）。 */
const noteSlot = (e: AttributionHistoryEntry): string => {
  const slot = slotOf(e);
  if (!slot?.l2_code) return '';
  return `面向 ${slot.l1_label || slot.l1_code} › ${slot.l2_label || slot.l2_code}`;
};

/**
 * 面向當下的狀態提示。
 *
 * 備註綁**面向**不綁那一列歸因，所以歸因被改成別的分類 / 被標記誤判之後備註依然在——這是刻意的
 * （搬走等於改寫歷史），但畫面上必須講清楚，否則使用者會以為備註掉了。`live` 不提示：一切正常
 * 時多一行字只是雜訊。
 */
const SLOT_STATE_HINT: Record<string, string> = {
  dismissed: '該面向已標記為 AI 誤判',
  gone: '此面向目前已無歸因',
};
const noteSlotHint = (e: AttributionHistoryEntry): string =>
  SLOT_STATE_HINT[slotOf(e)?.state ?? ''] ?? '';

/** 備註屬於人工紀錄：只在含該段的視圖顯示輸入區，初判／判決視圖不放（免得以為備註分階段）。 */
const canAddNote = computed(() => (TIMELINE_SCOPE_KINDS[scope.value] ?? ['note']).includes('note'));

const DOT_COLOR: Record<string, string> = {
  prejudge: 'rgb(var(--primary-6))', // 藍＝AI 初判快照
  note: 'var(--color-neutral-6)', // 灰＝備註
  failure: 'rgb(var(--danger-6))', // 紅＝初判失敗
  correction: 'rgb(var(--warning-6))', // 橙＝人工糾正（改／增／標記誤判／還原）
  review_confirm: 'rgb(var(--success-6))', // 綠＝複審確認 AI 判對
  suggestion: 'rgb(var(--purple-6))', // 紫＝重判轉待審建議（現值未動）
  suggestion_resolved: 'rgb(var(--success-6))', // 綠＝建議已處置
};

/** 人工糾正事件的操作文案（params.op）。 */
const CORRECTION_OP_TEXT: Record<string, string> = {
  update: '修改歸因',
  create: '新增歸因',
  delete: '標記為 AI 誤判',
  restore: '還原歸因',
};

/** 人工事件的一行摘要（含欄位級 delta——這正是之後要回餵 Prompt 迭代的判準）。 */
const manualText = (e: AttributionHistoryEntry): string => {
  const p = (e.params ?? {}) as {
    op?: string;
    changed?: Record<string, [unknown, unknown]>;
    confirmed_fields?: string[];
    counts?: Record<string, number>;
    applied?: number;
    rejected?: number;
  };
  if (e.kind === 'correction') {
    const delta = Object.entries(p.changed ?? {})
      .map(([k, [from, to]]) => `${FIELD_LABELS[k] || k} ${from ?? '—'} → ${to ?? '—'}`)
      .join('；');
    return [CORRECTION_OP_TEXT[p.op || ''] || '人工糾正', delta].filter(Boolean).join('：');
  }
  if (e.kind === 'review_confirm') {
    const fields = (p.confirmed_fields ?? []).map((f) => FIELD_LABELS[f] || f).join('、');
    return fields ? `確認正確：${fields}` : '確認 AI 判定正確';
  }
  if (e.kind === 'suggestion') {
    const c = p.counts ?? {};
    const parts = Object.entries(c)
      .filter(([, n]) => n)
      .map(([k, n]) => `${CHANGE_TYPE_LABELS[k] || k} ${n}`)
      .join('、');
    return `本則已人工託管，重新初判結果轉為待審建議（${parts || '無差異'}）——現值未變動`;
  }
  return `處置待審建議：採納 ${p.applied ?? 0} 條、駁回 ${p.rejected ?? 0} 條`;
};

/** 初判快照單筆（後端 snapshot_of 形狀；寬鬆型別容忍回填/新版欄位差異）。 */
type Snap = {
  polarity?: string;
  sentiment_score?: number | null;
  l1?: { code?: string; label?: string };
  l2?: { code?: string; label?: string };
  confidence?: { value?: number | null; tier?: string };
  content?: { summary?: unknown };
  is_primary?: boolean;
};

const snapsOf = (e: AttributionHistoryEntry): Snap[] => (e.attributions as Snap[] | null) ?? [];

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

/** L1›L2 麵包屑（缺層自動略過）。 */
const snapPath = (s: Snap): string =>
  [s.l1?.label, s.l2?.label].filter(Boolean).join(' › ') || '未歸因';

/** 快照結構鍵（分類變化對比用：傾向+情緒分+L1-L2 code，排序後串接）。 */
const structKey = (snaps: Snap[]): string =>
  snaps
    .map((s) => `${s.polarity}|${s.sentiment_score}|${s.l1?.code || ''}|${s.l2?.code || ''}`)
    .sort()
    .join(';');

/**
 * 初判事件 vs 前一次初判的變更徽章（oldest→newest 逐筆對比；首筆回「初次初判」）。
 * 每次初判都留一列（後端不去重），結果與前一次完全相同者由後端標 `params.unchanged`
 * → 顯示「重跑·無變化」；其餘逐項報模型/歸因數/分類，僅措辭信心漂移歸「內容微調」。
 */
const changesById = computed<Record<number, string[]>>(() => {
  const attributions = list.value.filter((e) => e.kind === 'prejudge'); // 已是 oldest→newest（後端 ASC）
  const out: Record<number, string[]> = {};
  attributions.forEach((e, i) => {
    if (i === 0) {
      out[e.id] = [(e.params as any)?.backfilled ? '初始回填' : '初次初判'];
      return;
    }
    if ((e.params as any)?.unchanged) {
      out[e.id] = ['重跑 · 無變化']; // 後端標記：模型/參數/結果與前一次完全相同
      return;
    }
    const prev = attributions[i - 1];
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

/** 初判失敗事件文案：後端 `insert_failure_event` 寫入 `params={error}`。 */
const failureText = (e: AttributionHistoryEntry): string => {
  const p = (e.params ?? {}) as { error?: string };
  return p.error || '初判失敗（未記錄原因）';
};
</script>

<template>
  <a-drawer
    v-model:visible="open"
    :title="scopeTitle"
    :footer="false"
    :width="860"
    unmount-on-close
    :body-style="{ display: 'flex', flexDirection: 'column', overflow: 'hidden' }"
  >
    <div class="flex min-h-0 flex-1 gap-5">
      <!-- 左：該反饋的單一事件時間軸，依 scope 過濾出當前階段視圖；佈局 7:3（無備註輸入時獨佔） -->
      <div class="flex min-w-0 flex-[7] flex-col">
        <StateGuard :loading="loading" error="">
          <!-- 滾動容器包在 a-timeline 外層：.arco-timeline 是 flex column、item 有 min-height 78px，
               若把 max-h+overflow 直接掛在 timeline 上，超高時 flex-shrink 會把各 item 壓到下限
               → 高內容溢出蓋到下一項（時間軸堆疊 bug）。外包一層讓 timeline 自然撐高、由外層滾動。 -->
          <ScrollFadeArea v-if="visibleList.length" class="min-h-0 flex-1">
            <a-timeline class="pl-1 pr-2">
              <!-- 複合 key：備註與事件來自兩張表，id 各自獨立會撞（見 list_attribution_history 註解） -->
              <a-timeline-item v-for="e in visibleList" :key="`${e.kind}-${e.id}`" :dot-color="DOT_COLOR[e.kind]">
                <!-- 首行：時間 + 事件身分 -->
                <div
                  class="flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-[var(--color-text-3)]"
                >
                  <span>{{ fmtTimelineTime(e.created_at) }}</span>
                  <template v-if="e.kind === 'prejudge'">
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
                    <span v-if="e.triggered_by">by {{ formatActor(e.triggered_by) }}</span>
                    <a-button
                      v-if="e.job_id"
                      size="mini"
                      type="text"
                      @click="openRunLog(e.job_id!)"
                    >
                      <template #icon><icon-code /></template>
                      查看 LLM 日誌
                    </a-button>
                  </template>
                  <template v-else-if="e.kind === 'failure'">
                    <a-tag size="small" color="red">初判失敗</a-tag>
                    <span v-if="e.triggered_by">by {{ formatActor(e.triggered_by) }}</span>
                  </template>
                  <template v-else>
                    <!-- 備註：類型標籤 + 所屬面向。有面向＝面向備註（綁 L1›L2，跨重判存活），
                         無面向＝整則備註。面向已消失時仍顯示，並標明它現在沒有對應的歸因——
                         那是有意義的歷史（「這個面向當初被判過、後來被改掉了」），不是壞資料。 -->
                    <a-tag size="small" :color="noteTypeColor(e)">{{ noteTypeLabel(e) }}</a-tag>
                    <span v-if="noteSlot(e)" class="text-[var(--color-text-3)]">
                      {{ noteSlot(e) }}
                    </span>
                    <a-tag v-if="noteSlotHint(e)" size="small" color="gray">
                      {{ noteSlotHint(e) }}
                    </a-tag>
                    <span class="font-medium text-[var(--color-text-2)]">{{
                      formatActor(e.author)
                    }}</span>
                  </template>
                </div>
                <!-- 內容：依事件類型 -->
                <div v-if="e.kind === 'prejudge'" class="mt-1 flex flex-col gap-1">
                  <div
                    v-for="(s, si) in snapsOf(e)"
                    :key="si"
                    class="rounded bg-[var(--color-fill-1)] px-2 py-1 text-xs leading-snug"
                  >
                    <div class="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                      <span class="font-medium text-[rgb(var(--primary-6))]">{{
                        snapPath(s)
                      }}</span>
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
                </div>
                <!-- 初判失敗：後端 insert_failure_event 寫 params={error}；此分支缺席時這些事件
                     會落到下方 v-else 被當成備註，渲染出 author/content 都空的灰色空白列 -->
                <div
                  v-else-if="e.kind === 'failure'"
                  class="mt-0.5 whitespace-pre-wrap text-xs leading-snug text-[rgb(var(--danger-6))]"
                >
                  {{ failureText(e) }}
                </div>
                <!-- 人工介入四種事件（correction / review_confirm / suggestion / suggestion_resolved）。
                     這個分支缺席的話它們會落到下方 v-else 被當成備註，渲染出 author/content 皆空的
                     灰色空白列——`failure` 曾經就這樣在時間軸上假冒了 390 筆備註。 -->
                <div
                  v-else-if="MANUAL_KINDS.includes(e.kind)"
                  class="mt-0.5 flex flex-col gap-0.5 text-xs leading-snug"
                >
                  <span class="text-[var(--color-text-1)]">{{ manualText(e) }}</span>
                  <span v-if="e.content" class="text-[var(--color-text-3)]">
                    理由：{{ e.content }}
                  </span>
                </div>
                <div
                  v-else
                  class="mt-0.5 whitespace-pre-wrap text-xs leading-snug text-[var(--color-text-1)]"
                >
                  {{ e.content }}
                </div>
              </a-timeline-item>
            </a-timeline>
          </ScrollFadeArea>
          <a-empty v-else :description="scopeEmpty" />
        </StateGuard>
      </div>
      <!-- 右：新增反饋級備註（佔 3/10）。備註屬於「人工歷史」段，初判／判決視圖不顯示輸入區
           ——否則會讓人以為備註是分階段各留一份，但它是反饋級的、只有一份。 -->
      <div
        v-if="canAddNote"
        class="flex min-w-0 flex-[3] flex-col gap-2 border-l border-[var(--color-neutral-3)] pl-5"
      >
        <!-- 互動類型：值域走 attribution_dimension_master 的 note_type 軸，業務可自行增減
             （「已聯繫供應商」「待跟進」這類類型會隨作業流程演進，不該寫死在碼裡）。 -->
        <a-select
          v-model="draftType"
          size="small"
          :options="noteTypes.map((t) => ({ value: t.item_code, label: t.item_label }))"
        />
        <a-textarea
          v-model="draft"
          :auto-size="{ minRows: 4 }"
          :max-length="500"
          show-word-limit
          placeholder="記錄這則反饋的處理脈絡"
        />
        <!-- append-only：寫出去就定了。文案用「留下」的語感建立正確預期，不做撤回功能——
             備註是互動軌跡，「已聯繫供應商」若能事後改成別的，這條軌跡的稽核價值就沒了。 -->
        <span class="text-[11px] text-[var(--color-text-3)]">送出後無法修改或撤回。</span>
        <div class="flex justify-end">
          <a-button
            type="primary"
            size="small"
            :loading="saving"
            :disabled="!draft.trim()"
            @click="submitNote"
          >
            <template #icon><icon-send /></template>
            送出備註
          </a-button>
        </div>
      </div>
    </div>

    <!-- 「查看 LLM 日誌」：讀落庫快照的歷史回看模式（與初判分類即時抽屜共用同一元件）-->
    <!-- 單筆視角：帶 item-id 過濾批量快照，直達這則反饋的日誌（批量抽屜入口不帶＝整批視角） -->
    <PrejudgeLogDrawer
      v-model:visible="logDrawerVisible"
      :job-id="logDrawerJobId"
      :item-id="sourceId"
    />
  </a-drawer>
</template>

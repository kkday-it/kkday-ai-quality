<script setup lang="ts">
/**
 * 人工糾正工作台（**反饋級**：一則反饋的全部歸因排成一張表，逐條調整）。
 *
 * 舊版是「單條歸因的表單」，寫死取 `record.attributions[0]`——一則反饋有多條歸因時第二條以後
 * 完全碰不到。正確的心智模型是「這則反饋現在有這些分類，我要調整它們」：看得到全貌、挑任一條
 * 改、補上 AI 漏判的、標記誤判的，**條數可增可減**。
 *
 * 版面三個刻意的決定：
 *
 * 1. **沒有全域送出鍵**（`:footer="false"`）——每一列各自生效。抽屜底部若有一顆「確定」，
 *    使用者會以為改動要按了那顆才算數，但這裡是逐條提交（理由見 composable docstring）。
 * 2. **編輯區在表格下方，不是行內展開**——編輯表單需要完整寬度（分類 cascader + 情緒分 +
 *    理由），塞進表格列會被欄寬切割；而且固定在下方時，改哪一列由上方高亮指示，全貌不會被推走。
 * 3. **標記誤判／還原也走同一個編輯區**，不用 `Modal.confirm` 帶輸入框——理由是必填欄位，
 *    塞進 confirm 的 content 需要 JSX（`<script setup>` 不支援），且破壞性操作的說明文字
 *    在抽屜內鋪得開。
 *
 * 狀態全在 `useAttributionCorrection`，本檔只留 template 與純顯示衍生值。
 */
import { computed, nextTick, reactive, ref, watch } from 'vue';
import { IconCheckCircle, IconCloseCircle, IconEdit, IconEye, IconMessage, IconPlus, IconRefresh, IconSend, IconSwap, IconUndo } from '@arco-design/web-vue/es/icon';
import { type CascadeNode, getTaxonomyCascade } from '@/api';
import { AsyncSection, TableLayout } from '@/components';
import { POLARITY_LABELS, type Attribution } from '../constants';
import {
  attributionLines,
  attributionPath,
  fmtTimelineTime,
  formatActor,
  markOccupiedSlots,
} from '../utils';
import type { useAttributionCorrection } from '../composables';

const props = defineProps<{
  /** useAttributionCorrection 的回傳（由呼叫端持有，抽屜與列表共用同一份狀態）。 */
  ctl: ReturnType<typeof useAttributionCorrection>;
  /** 是否有複審權限（`attribution.review`）——與糾正是**不同的**權限鍵，不可共用一個旗標。 */
  canReview?: boolean;
}>();
const emit = defineEmits<{ suggestions: [] }>();

// 解構成區域常數：ref 身分不變（仍是呼叫端那一份狀態），但 template 引用的是 local 而非 prop
// ——直接在 template 寫 `ctl.open.value = v` 會被 vue/no-mutating-props 判成改 prop。
const {
  open,
  target,
  policy,
  loading,
  error,
  stale,
  live,
  deleted,
  humanManaged,
  suggestionCount,
  occupiedSlots,
  busyOid,
  editingOid,
  creating,
  justChangedOid,
  editDraft,
  createDraft,
  sessionLog,
  lastReason,
  noteTypes,
  notesForSlot,
  addSlotNote,
  canSubmitEdit,
  canSubmitCreate,
  reload,
  startEdit,
  startCreate,
  cancelEdit,
  submitEdit,
  submitCreate,
  dismiss,
  restore,
  swapWith,
  confirmCorrect,
} = props.ctl;

/** 底部編輯區當前在做什麼；`null`＝收合。 */
const op = ref<'edit' | 'dismiss' | 'restore' | 'note' | null>(null);
const opRow = ref<Attribution | null>(null);

/** 面向備註的草稿（送出後清空；**沒有草稿保存**——備註是 append-only，寫了就該發出去）。 */
const noteDraft = reactive({ note_type: '', content: '' });
const noteBusy = ref(false);

const cascade = ref<CascadeNode[]>([]);
watch(open, async (v) => {
  if (v && !cascade.value.length) cascade.value = await getTaxonomyCascade();
  if (!v) op.value = null;
});

/**
 * 表格資料：有效列在前、tombstone 在後。
 *
 * tombstone 排在後面是為了讓「現在有效的有幾條」在視線上端是連續的一塊——夾在中間的話，
 * 使用者得逐列判讀灰底才數得出來。
 */
const rows = computed(() => [
  ...live.value.map((a) => ({ ...a, _dismissed: false })),
  ...deleted.value.map((a) => ({ ...a, _dismissed: true })),
]);

const COLUMNS = [
  { title: '歸因', slotName: 'attr', width: 290 },
  { title: '判定', slotName: 'judge', width: 190 },
  { title: '狀態', slotName: 'state', width: 120 },
  { title: '操作', slotName: 'ops', width: 150 },
];

/**
 * 剛動過的列自動捲入視野。
 *
 * 高亮本身（`.attr-row-changed`）解決不了「列在視窗外」——工作台一則反饋可能有 6 條歸因，
 * 改到下面幾條時，成功回饋亮在使用者根本看不到的地方。DOM 操作放元件不放 composable：
 * class 名與表格結構都是這裡的實作細節。
 */
watch(justChangedOid, async (oid) => {
  if (oid == null) return;
  await nextTick();
  // 文件級查詢在這裡是安全的：`.attr-row-changed` 只由本元件的 rowClass 產生，
  // 且同時只會開一個工作台（列表的「人工糾正」共用單一 ctl 狀態）。
  document.querySelector('.attr-row-changed')?.scrollIntoView({
    block: 'nearest',
    behavior: 'smooth',
  });
});

/** 列樣式：tombstone 灰顯、剛動過的黃底高亮、正在編輯的列標出來。 */
const rowClass = (record: Record<string, unknown>) => {
  const oid = record.attribution_oid as number;
  return [
    record._dismissed ? 'attr-row-dismissed' : '',
    justChangedOid.value === oid ? 'attr-row-changed' : '',
    editingOid.value === oid ? 'attr-row-editing' : '',
  ]
    .filter(Boolean)
    .join(' ');
};

/** 分類 cascader 的選項：把已佔用的面向標出來（判準與踩過的坑見 `markOccupiedSlots`）。 */
const cascadeFor = (excludeOid: number | null, swappable = false) =>
  markOccupiedSlots(cascade.value, occupiedSlots.value, excludeOid, swappable);

/**
 * 情緒分 → 傾向的即時預覽。
 *
 * 傾向**不是輸入項**：它由情緒分派生（與後端 SENTIMENT_BANDS 同一份區間），讓人另外選傾向會
 * 出現「正向＋情緒分 1」這種矛盾組合，而且沒人知道兩個欄位誰說了算。這裡只做預覽，落庫以後端為準。
 */
const polarityOf = (n: number | undefined) => {
  if (!n) return '';
  const code = n <= 2 ? 'negative' : n === 3 ? 'neutral' : 'positive';
  return POLARITY_LABELS[code] || code;
};

/**
 * L2 code → 面向名（cascade 樹是扁平找不到的巢狀結構，這裡攤平成一張表供 delta 用）。
 *
 * 少了這層，delta 會顯示 `面向 餐飲品質 → C-1-1`——右邊是代碼、左邊是名字，兩邊對不上就失去
 * 「不必自己描述改了什麼」的作用。
 */
const l2LabelOf = computed(() => {
  const map = new Map<string, string>();
  for (const l1 of cascade.value) {
    for (const l2 of l1.children ?? []) map.set(l2.value, l2.label);
  }
  return map;
});

/** 編輯中的欄位級 delta（唯讀灰字）——「改了什麼」系統已知，人只需要寫「為什麼」。 */
const deltaText = computed(() => {
  const a = opRow.value;
  if (!a) return '';
  const parts: string[] = [];
  if (editDraft.l2_code && editDraft.l2_code !== a.l2?.code) {
    const to = l2LabelOf.value.get(editDraft.l2_code) || editDraft.l2_code;
    parts.push(`面向 ${a.l2?.label || a.l2?.code || '—'} → ${to}`);
  }
  if (editDraft.sentiment_score && editDraft.sentiment_score !== a.sentiment_score) {
    parts.push(`情緒分 ${a.sentiment_score ?? '—'} → ${editDraft.sentiment_score}`);
  }
  return parts.join(' · ');
});

const openEdit = (a: Attribution) => {
  op.value = 'edit';
  opRow.value = a;
  startEdit(a);
};
const openDismiss = (a: Attribution) => {
  op.value = 'dismiss';
  opRow.value = a;
  startEdit(a);
};
const openRestore = (a: Attribution) => {
  op.value = 'restore';
  opRow.value = a;
  startEdit(a);
};
/**
 * 對這一列的**面向**留備註。
 *
 * 不呼叫 `startEdit`——備註不是糾正，不佔用「編輯中的列」那個單開槽位，也不需要理由門檻。
 * 預設類型取值域第一項（`internal` 內部說明），最常見的情況一鍵就能寫。
 */
const openNote = (a: Attribution) => {
  op.value = 'note';
  opRow.value = a;
  cancelEdit();
  noteDraft.note_type = noteTypes.value[0]?.item_code || '';
  noteDraft.content = '';
};
const submitNote = async () => {
  const l1 = opRow.value?.l1?.code;
  const l2 = opRow.value?.l2?.code;
  if (!l1 || !l2 || !noteDraft.note_type || !noteDraft.content.trim()) return;
  noteBusy.value = true;
  const ok = await addSlotNote(l1, l2, noteDraft.note_type, noteDraft.content);
  noteBusy.value = false;
  if (ok) noteDraft.content = '';
};
const openCreate = () => {
  op.value = null;
  opRow.value = null;
  startCreate();
};
const closePanel = () => {
  op.value = null;
  opRow.value = null;
  cancelEdit();
};

/**
 * 編輯中選到的面向是否已被**另一條存活歸因**佔用 → 回那一條的 oid（可互換）。
 *
 * `cascadeFor(oid, true)` 刻意讓這種選項保持可選（tombstone 佔用的才 disable），選中即在這裡
 * 把「你要的是互換嗎」問出來——這正是「AI 把兩個面向寫反了」的場景，也是逐條提交唯一解不開的
 * 死結（後端靠 DEFERRABLE 約束在單一交易內完成）。選中後普通送出必撞 409，故送出鍵同步鎖住
 * （見 `blockedBySwap`），只留互換這一個出口。
 *
 * tombstone 佔用的面向不提供互換：它身上的誤判理由指的是舊面向，搬走會讓那句話變成謊言。
 */
const swapCandidate = computed(() => {
  if (op.value !== 'edit' || !editDraft.l2_code) return null;
  const hit = occupiedSlots.value.get(editDraft.l2_code);
  if (!hit || hit.dismissed || hit.oid === opRow.value?.attribution_oid) return null;
  return live.value.find((a) => a.attribution_oid === hit.oid) ?? null;
});

/** 選中的面向只能靠互換取得 → 普通送出必撞 409，鎖住送出鍵，把人導到互換那顆。 */
const blockedBySwap = computed(() => swapCandidate.value !== null);

const reasonMin = computed(() => policy.value?.reason_min_length ?? 5);
const reasonMax = computed(() => policy.value?.reason_max_length ?? 500);
const reasonOkFor = (text: string) => text.trim().length >= reasonMin.value;

const submitPanel = async () => {
  // `attribution_oid` 在共用型別上是 optional（待審建議的 add 提案還沒落庫），但工作台的列一律
  // 來自持久化資料，必有值；缺值時直接不送，不硬轉型。
  const oid = opRow.value?.attribution_oid;
  if (!oid) return;
  const reason = editDraft.reason.trim();
  if (op.value === 'edit') await submitEdit(oid);
  else if (op.value === 'dismiss') await dismiss(oid, reason);
  else if (op.value === 'restore') await restore(oid, reason);
  op.value = null;
  opRow.value = null;
};

/** 互換兩條歸因的面向（理由沿用編輯區已填的那一段——互換本身就是一次糾正）。 */
const doSwap = async () => {
  const mine = opRow.value?.attribution_oid;
  const other = swapCandidate.value?.attribution_oid;
  if (!mine || !other) return;
  await swapWith(mine, other, editDraft.reason.trim());
  op.value = null;
  opRow.value = null;
};

const PANEL_TITLE: Record<string, string> = {
  edit: '修改歸因',
  dismiss: '標記為 AI 誤判',
  restore: '還原歸因',
  note: '面向備註',
};

/** 互動類型 code → 顯示名（值域是業務可改的參照資料，查不到就原樣顯示 code 而非空白）。 */
const noteTypeLabel = (code: string) =>
  noteTypes.value.find((t) => t.item_code === code)?.item_label || code;
</script>

<template>
  <a-drawer
    :visible="open"
    :width="900"
    :footer="false"
    unmount-on-close
    :body-style="{ display: 'flex', flexDirection: 'column', overflow: 'hidden', padding: '12px 16px' }"
    @update:visible="(v: boolean) => (open = v)"
  >
    <template #title>
      <span>人工糾正</span>
      <a-tag size="small" class="ml-2 font-mono">#{{ target.sourceId }}</a-tag>
      <a-tag size="small" color="green" class="ml-1">有效 {{ live.length }} 條</a-tag>
      <a-tag v-if="deleted.length" size="small" color="gray" class="ml-1">
        已標記誤判 {{ deleted.length }} 條
      </a-tag>
    </template>

    <div class="flex min-h-0 flex-1 flex-col gap-2">
      <!-- 畫面過期：該反饋在工作台開著的期間被重新初判，attribution_oid 已全部換新。
           不自動重載——使用者可能正在打字，直接洗掉輸入比讓他自己按更糟。 -->
      <a-alert v-if="stale" type="error" class="!mb-0">
        這則反饋剛被重新初判，畫面上的資料已過期。
        <template #action>
          <a-button size="mini" @click="reload()"><template #icon><icon-refresh /></template>重新載入</a-button>
        </template>
      </a-alert>

      <!-- 待審建議：與本抽屜操作同一份資料，兩個入口必須互相看得見 -->
      <a-alert v-if="suggestionCount" type="warning" class="!mb-0">
        AI 對這則反饋有 {{ suggestionCount }} 條新建議尚未處理。
        <template #action>
          <a-button size="mini" @click="emit('suggestions')"><template #icon><icon-eye /></template>查看待審建議</a-button>
        </template>
      </a-alert>

      <!-- AI 託管警示：先告知風險，出事（送出時 404）使用者才知道發生了什麼 -->
      <a-alert v-if="!humanManaged && !stale" type="normal" class="!mb-0">
        這則反饋尚未有人工介入，批量重新初判仍會覆蓋現值。你的第一次提交會鎖定它。
      </a-alert>

      <a-alert v-if="sessionLog.length" type="success" class="!mb-0">
        本次已提交 {{ sessionLog.length }} 項變更：{{ sessionLog.map((s) => s.label).join('、') }}
      </a-alert>

      <AsyncSection
        :loading="loading"
        :error="error"
        :empty="!rows.length"
        empty-text="這則反饋還沒有任何歸因；可用下方「新增遺漏歸因」補上"
      >
        <div class="min-h-0 flex-1 overflow-hidden">
          <TableLayout
            full-height
            :data="rows"
            :columns="COLUMNS"
            :pagination="false"
            row-key="attribution_oid"
            :row-class="rowClass"
          >
            <template #attr="{ record }">
              <div class="flex flex-col gap-1 text-xs">
                <div v-if="record.content?.summary" class="line-clamp-2 font-medium">
                  {{ record.content.summary }}
                </div>
                <div class="flex gap-1.5">
                  <span class="shrink-0 text-[var(--color-text-3)]">歸因</span>
                  <span class="min-w-0 truncate" :title="attributionPath(record)">
                    {{ attributionPath(record) }}
                  </span>
                </div>
                <div v-if="record.content?.evidence" class="flex gap-1.5">
                  <span class="shrink-0 text-[var(--color-text-3)]">佐證</span>
                  <span class="min-w-0 truncate" :title="record.content.evidence">
                    {{ record.content.evidence }}
                  </span>
                </div>
              </div>
            </template>

            <template #judge="{ record }">
              <div class="flex flex-col gap-1 text-xs">
                <div
                  v-for="l in attributionLines(record).filter((x) => x.k !== '歸因')"
                  :key="l.k"
                  class="flex gap-1.5"
                >
                  <span class="shrink-0 text-[var(--color-text-3)]">{{ l.k }}</span>
                  <span class="min-w-0 truncate" :title="l.v">{{ l.v }}</span>
                </div>
              </div>
            </template>

            <template #state="{ record }">
              <div class="flex flex-col items-start gap-1">
                <a-tag v-if="record._dismissed" size="small" color="gray" :title="record.correction_reason">
                  已標記誤判
                </a-tag>
                <a-tag v-if="record.origin === 'human'" size="small" color="orange">人工</a-tag>
                <a-tag v-if="record.review_status === 'confirmed'" size="small" color="green">
                  已確認正確
                </a-tag>
                <span v-if="record.correction_reason" class="text-[10px] text-[var(--color-text-3)] line-clamp-2">
                  {{ record.correction_reason }}
                </span>
              </div>
            </template>

            <!-- per-row 操作一律 type="text"（見 frontend-vue.md 的 per-row 例外條款）；
                 loading 綁 per-row 的 busyOid，全域一顆會讓整張表一起轉圈。 -->
            <template #ops="{ record }">
              <div class="flex flex-wrap gap-x-3 gap-y-1">
                <template v-if="record._dismissed">
                  <a-button
                    type="text"
                    size="mini"
                    class="!px-0"
                    :loading="busyOid === record.attribution_oid"
                    @click="openRestore(record)"
                  >
                    <template #icon><icon-undo /></template>
                    還原
                  </a-button>
                </template>
                <template v-else>
                  <a-button
                    type="text"
                    size="mini"
                    class="!px-0"
                    :loading="busyOid === record.attribution_oid"
                    @click="openEdit(record)"
                  >
                    <template #icon><icon-edit /></template>
                    修改
                  </a-button>
                  <a-button
                    type="text"
                    size="mini"
                    class="!px-0"
                    :disabled="!canReview || record.review_status === 'confirmed'"
                    :loading="busyOid === record.attribution_oid"
                    @click="confirmCorrect(record.attribution_oid)"
                  >
                    <template #icon><icon-check-circle /></template>
                    確認正確
                  </a-button>
                  <a-button
                    type="text"
                    size="mini"
                    status="danger"
                    class="!px-0"
                    :loading="busyOid === record.attribution_oid"
                    @click="openDismiss(record)"
                  >
                    <template #icon><icon-close-circle /></template>
                    標記誤判
                  </a-button>
                </template>
                <!-- 面向備註：tombstone 也留著（那條誤判理由之外的補充說明同樣有價值），
                     且備註綁面向不綁列，標記誤判不該讓已寫的話沒地方看。 -->
                <a-button type="text" size="mini" class="!px-0" @click="openNote(record)">
                  <template #icon><icon-message /></template>
                  備註<template v-if="notesForSlot(record.l2?.code).length">
                    · {{ notesForSlot(record.l2?.code).length }}
                  </template>
                </a-button>
              </div>
            </template>
          </TableLayout>
        </div>
      </AsyncSection>

      <!-- ── 底部編輯區：改 / 標記誤判 / 還原共用；新增獨立一塊 ────────────────── -->
      <div v-if="op && opRow" class="flex-none rounded border border-[var(--color-neutral-3)] p-3">
        <div class="mb-2 flex items-center gap-2">
          <span class="text-sm font-medium">{{ PANEL_TITLE[op] }}</span>
          <span class="min-w-0 truncate text-xs text-[var(--color-text-3)]">
            {{ attributionPath(opRow) }}
          </span>
        </div>

        <!-- 面向備註：這條時間軸綁的是**面向**不是這一列，所以重新初判後仍在。
             append-only（不可撤回不可編輯），送出鍵文案刻意用「留下備註」而非「儲存」——
             留言語感建立「發出去就在那了」的正確預期，打錯就再補一則說明。 -->
        <template v-if="op === 'note'">
          <div class="mb-2 max-h-40 overflow-auto rounded bg-[var(--color-fill-1)] p-2">
            <div
              v-if="!notesForSlot(opRow.l2?.code).length"
              class="py-2 text-center text-xs text-[var(--color-text-3)]"
            >
              這個面向還沒有備註。留下的話會依時間排進這則反饋的完整時間線。
            </div>
            <div
              v-for="n in notesForSlot(opRow.l2?.code)"
              :key="n.attribution_note_oid"
              class="border-b border-[var(--color-neutral-3)] py-1.5 last:border-0"
            >
              <div class="flex items-center gap-2 text-[11px] text-[var(--color-text-3)]">
                <a-tag size="small">{{ noteTypeLabel(n.note_type) }}</a-tag>
                <span>{{ formatActor(n.author) }}</span>
                <span>{{ fmtTimelineTime(n.created_at) }}</span>
              </div>
              <div class="whitespace-pre-wrap text-xs">{{ n.content }}</div>
            </div>
          </div>
          <div class="flex items-start gap-2">
            <a-select v-model="noteDraft.note_type" size="small" class="w-36 flex-none">
              <a-option v-for="t in noteTypes" :key="t.item_code" :value="t.item_code">
                {{ t.item_label }}
              </a-option>
            </a-select>
            <a-textarea
              v-model="noteDraft.content"
              :max-length="1000"
              :auto-size="{ minRows: 2, maxRows: 4 }"
              placeholder="例如「已聯繫供應商確認房型，對方承認照片是舊的，本週更新」"
            />
          </div>
          <div class="mt-2 flex items-center gap-2">
            <span class="text-[11px] text-[var(--color-text-3)]">
              備註送出後不可撤回也不可編輯——它是互動軌跡，能改就失去稽核價值。
            </span>
            <div class="flex-1" />
            <a-button size="mini" @click="closePanel()">關閉</a-button>
            <a-button
              type="primary"
              size="mini"
              :loading="noteBusy"
              :disabled="!noteDraft.note_type || !noteDraft.content.trim()"
              @click="submitNote()"
            >
              <template #icon><icon-send /></template>
              留下備註
            </a-button>
          </div>
        </template>

        <a-form v-if="op === 'edit'" :model="editDraft" layout="vertical" size="small">
          <a-row :gutter="[12, 0]">
            <a-col :span="14">
              <a-form-item label="改為（不動就留空）">
                <a-cascader
                  v-model="editDraft.l2_code"
                  :options="cascadeFor(opRow.attribution_oid ?? null, true)"
                  allow-search
                  allow-clear
                  placeholder="選擇 歸因域 › 面向"
                  class="w-full"
                  expand-trigger="hover"
                />
              </a-form-item>
            </a-col>
            <a-col :span="10">
              <a-form-item label="情緒分">
                <div class="flex flex-col gap-1">
                  <a-input-number
                    v-model="editDraft.sentiment_score"
                    :min="1"
                    :max="5"
                    placeholder="不動就留空"
                    class="w-32"
                  />
                  <span class="text-[11px] text-[var(--color-text-3)]">
                    1-2 負向｜3 中立｜4-5 正向
                    <template v-if="polarityOf(editDraft.sentiment_score)">
                      — 會判為
                      <strong class="text-[var(--color-text-1)]">
                        {{ polarityOf(editDraft.sentiment_score) }}
                      </strong>
                    </template>
                  </span>
                </div>
              </a-form-item>
            </a-col>
          </a-row>
        </a-form>

        <a-alert v-else-if="op === 'dismiss'" type="warning" class="!mb-2">
          標記為 AI 誤判後，這條歸因會從列表與所有統計中消失，但<strong>紀錄會保留</strong>——
          之後隨時可以還原，而且能防止重新初判把它悄悄判回來。
        </a-alert>
        <a-alert v-else-if="op === 'restore'" type="normal" class="!mb-2">
          還原後這條歸因會回到列表與統計中。
        </a-alert>

        <!-- 理由區塊只屬於糾正三動作；備註有自己的送出流程（無理由門檻、無「沿用上一條」）。 -->
        <template v-if="op !== 'note'">
        <!-- delta 唯讀前綴：「改了什麼」系統已知（後端事件流存的就是它），人只需要寫「為什麼」 -->
        <div v-if="deltaText" class="mb-1 text-xs text-[var(--color-text-3)]">{{ deltaText }}</div>

        <!-- 選到別條佔著的面向＝多半是「兩條寫反了」。直接給互換，不讓使用者去撞 409，
             也不必走「先改成第三個暫時面向」的三步繞路。 -->
        <a-alert v-if="swapCandidate" type="info" class="!mb-2">
          這個面向目前屬於另一條歸因（{{ attributionPath(swapCandidate) }}）。要把兩條的面向對調嗎？
          <template #action>
            <a-button
              size="mini"
              type="outline"
              :disabled="!reasonOkFor(editDraft.reason) || stale"
              :loading="busyOid !== null"
              @click="doSwap()"
            >
              <template #icon><icon-swap /></template>
              與該條互換
            </a-button>
          </template>
        </a-alert>

        <a-textarea
          v-model="editDraft.reason"
          :max-length="reasonMax"
          show-word-limit
          :auto-size="{ minRows: 2, maxRows: 4 }"
          :placeholder="`理由（必填，至少 ${reasonMin} 字）：例如「AI 把出發時間誤解為集合時間，實際文意是集合」`"
        />
        <div class="mt-2 flex items-center gap-2">
          <span class="text-[11px] text-[var(--color-text-3)]">
            理由會成為 Prompt 迭代的判準，請寫具體原因而非「不對」。
          </span>
          <div class="flex-1" />
          <a-button
            v-if="lastReason && !editDraft.reason"
            type="text"
            size="mini"
            @click="editDraft.reason = lastReason"
          >
            沿用上一條理由
          </a-button>
          <a-button size="mini" @click="closePanel()">取消</a-button>
          <a-button
            type="primary"
            size="mini"
            :loading="busyOid === opRow.attribution_oid"
            :disabled="
              op === 'edit'
                ? !canSubmitEdit || blockedBySwap
                : !reasonOkFor(editDraft.reason) || stale
            "
            @click="submitPanel()"
          >
            {{ op === 'edit' ? '送出這一條' : PANEL_TITLE[op] }}
          </a-button>
        </div>
        </template>
      </div>

      <!-- 新增遺漏歸因：AI 沒判到的面向由人補上（那一列從頭到尾是人寫的，故摘要必填） -->
      <div v-if="creating" class="flex-none rounded border border-[var(--color-neutral-3)] p-3">
        <div class="mb-2 text-sm font-medium">新增遺漏歸因</div>
        <a-form :model="createDraft" layout="vertical" size="small">
          <a-row :gutter="[12, 0]">
            <a-col :span="14">
              <a-form-item label="歸因分類（必填）">
                <a-cascader
                  v-model="createDraft.l2_code"
                  :options="cascadeFor(null)"
                  allow-search
                  allow-clear
                  placeholder="選擇 歸因域 › 面向"
                  class="w-full"
                  expand-trigger="hover"
                />
              </a-form-item>
            </a-col>
            <a-col :span="10">
              <a-form-item label="情緒分（必填）">
                <div class="flex flex-col gap-1">
                  <a-input-number
                    v-model="createDraft.sentiment_score"
                    :min="1"
                    :max="5"
                    placeholder="1-5"
                    class="w-32"
                  />
                  <span class="text-[11px] text-[var(--color-text-3)]">
                    1-2 負向｜3 中立｜4-5 正向
                    <template v-if="polarityOf(createDraft.sentiment_score)">
                      — 會判為
                      <strong class="text-[var(--color-text-1)]">
                        {{ polarityOf(createDraft.sentiment_score) }}
                      </strong>
                    </template>
                  </span>
                </div>
              </a-form-item>
            </a-col>
          </a-row>
          <a-form-item label="摘要（必填）">
            <a-textarea
              v-model="createDraft.summary"
              :max-length="200"
              show-word-limit
              placeholder="用一句話寫出這條歸因在講什麼"
            />
          </a-form-item>
        </a-form>
        <a-textarea
          v-model="createDraft.reason"
          :max-length="reasonMax"
          show-word-limit
          :auto-size="{ minRows: 2, maxRows: 3 }"
          :placeholder="`理由（必填，至少 ${reasonMin} 字）：例如「AI 完全沒判到現場退款糾紛這個面向」`"
        />
        <div class="mt-2 flex items-center justify-end gap-2">
          <a-button
            v-if="lastReason && !createDraft.reason"
            type="text"
            size="mini"
            @click="createDraft.reason = lastReason"
          >
            沿用上一條理由
          </a-button>
          <a-button size="mini" @click="cancelEdit()">取消</a-button>
          <a-button type="primary" size="mini" :loading="busyOid === -1" :disabled="!canSubmitCreate" @click="submitCreate()"><template #icon><icon-plus /></template>
            新增這一條
          </a-button>
        </div>
      </div>

      <div v-else-if="!op" class="flex-none">
        <a-button type="outline" size="small" :disabled="stale" @click="openCreate()">
          <template #icon><icon-plus /></template>
          新增遺漏歸因
        </a-button>
      </div>
    </div>
  </a-drawer>
</template>

<style scoped>
/* tombstone 列：灰顯但仍可讀（要看得懂當初判了什麼才知道該不該還原），排序上已置於有效列之後。 */
:deep(.attr-row-dismissed) td {
  opacity: 0.55;
}
/* 剛動過的列：短暫黃底，取代「抽屜關掉了＝大概成功了」這種靠消失來表達成功的回饋。 */
:deep(.attr-row-changed) td {
  background: var(--color-warning-light-1);
}
:deep(.attr-row-editing) td {
  background: var(--color-primary-light-1);
}
</style>

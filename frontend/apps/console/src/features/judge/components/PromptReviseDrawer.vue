<script setup lang="ts">
/**
 * 案例庫 × AI 改寫**流水線**：① 選案例 → ② AI 改寫 → ③ 回歸驗證 → ④ 定案發布。
 *
 * 改造前這裡是三個平行 tab，動作被擺在「功能相近的分頁」而不是「該發生的時機」——「存為新草稿」
 * 放在「AI 改寫」分頁，但它在邏輯上要等回歸驗證通過，於是使用者被迫倒回上一個分頁才能把成果存下來
 * （使用者原話：「案例庫選擇 → AI改寫 → 套用補丁 → 回歸重跑 → 開始回歸 → **AI改寫** → 存為新草稿」，
 * 這條路徑本身就是診斷：它繞回去了）。
 *
 * 本抽屜是整條流水線的**狀態擁有者**：三個 composable 都在這裡呼叫、以 props 下傳給面板。這樣
 * 閘門判斷與「回退即失效」的判定全寫在一個地方，面板退化成純渲染，也不會出現「面板一份、抽屜
 * 又存一份」的雙份真相。
 */
import { computed, defineAsyncComponent, ref, toRef, watch } from 'vue';
import { Modal } from '@arco-design/web-vue';
import { TableLayout } from '@/components';
import {
  PERM,
  type PromptDraftMeta,
  type PromptReleaseMeta,
  type PromptVersionSaved,
} from '@/api';
import type { LlmOverrides } from '@/features/settings/types';
import { usePermission } from '@/composables/usePermission';
import { usePromptRegression, usePromptReviewCases, usePromptRevise } from '../composables';
import {
  clampStep,
  fmtDt,
  highestReachableStep,
  isRegressionStale,
  stepBlockedReason,
  type PipelineState,
  type PipelineStep,
  type RegressionValidity,
} from '../utils';

// 相對路徑 import（非走 barrel）：本檔自身即為 components barrel 的一員，經 barrel 迴繞會觸發
// circular dep。三個重面板都是切到該步驟才載。
const PromptRevisePanel = defineAsyncComponent(() => import('./PromptRevisePanel.vue'));
const PromptRegressionPanel = defineAsyncComponent(() => import('./PromptRegressionPanel.vue'));
const PromptReleaseStep = defineAsyncComponent(() => import('./PromptReleaseStep.vue'));

const props = defineProps<{
  visible: boolean;
  /** 頁面上當前的 Prompt 全文（改寫的基準）。 */
  systemPrompt: string;
  /** 當前正式版名。 */
  promptVersion: string;
  /** 當前正式版全文（步驟④的 diff 左側）。 */
  releasePrompt: string;
  /** 頁面上的 Prompt 已偏離最新版。 */
  promptEdited: boolean;
  /** 草稿/正式版清單（與頁面「版本列表」同一份資料源）。 */
  drafts: PromptDraftMeta[];
  releases: PromptReleaseMeta[];
  /** 外部要求開在哪一步（如「前往案例庫改寫」帶進來）；由 query 或父層設定。 */
  initialStep?: number;
  /** 開啟時預先勾選的案例 id（存完案例直接跳過來時用）。 */
  preselectId?: number;
}>();

const emit = defineEmits<{
  (e: 'update:visible', v: boolean): void;
  /** 案例總數變動（父層徽章用實數校正）。 */
  (e: 'count', n: number): void;
  /** 存出了新的 Prompt 版本或切換了線上口徑，父層需重載 defaults。 */
  (e: 'savedVersion'): void;
  /** 當前步驟變動（父層同步進 route query）。 */
  (e: 'stepChange', step: PipelineStep): void;
  /** 使用者要求改用「跑批」拉真實資料複驗：父層關本抽屜、開跑批抽屜。 */
  (e: 'requestBatch'): void;
}>();

const { can } = usePermission();
const canManage = computed(() => can(PERM.prejudgeRun));

// ── 流水線狀態（全部在抽屜層持有）──────────────────────────────────────────────
const cases = usePromptReviewCases();
const revise = usePromptRevise({
  systemPrompt: toRef(props, 'systemPrompt'),
  reviewIds: computed(() => cases.selectedIds.value),
});
const regression = usePromptRegression();

const step = ref<PipelineStep>(1);
/** ④ 存出的草稿名；空＝這輪還沒存過。 */
const savedDraft = ref('');
/**
 * 回歸執行**當下**的基準（驗的是哪份全文、哪組案例）。
 *
 * 這是本次改造最重要的一份狀態：沒有它，使用者可以「套補丁 A → 回歸綠燈 → 改成補丁 B → 直接
 * 發布」，等於拿 A 的綠燈發布了 B。它同時也讓「跑回歸時把標的改成 baseline/歷史版」這個覆寫
 * 路徑自動安全——驗的不是候選版，`stale` 就會是 true，④ 依然鎖著。
 */
const validatedAt = ref<RegressionValidity | null>(null);

const gateState = computed<PipelineState>(() => ({
  selectedIds: cases.selectedIds.value,
  candidatePrompt: revise.revisedPrompt.value,
  regressionDone: regression.snapshot.value?.status === 'done' && !stale.value,
}));

/** 回歸結果是否已對不上當前的候選版／案例集。 */
const stale = computed(() =>
  isRegressionStale(validatedAt.value, {
    candidatePrompt: revise.revisedPrompt.value,
    selectedIds: cases.selectedIds.value,
  }),
);

const STEPS = [
  { no: 1 as const, title: '選案例' },
  { no: 2 as const, title: 'AI 改寫' },
  { no: 3 as const, title: '回歸驗證' },
  { no: 4 as const, title: '定案發布' },
];

/** 步號右側的即時摘要（取代「要切過去才知道選了幾個」）。 */
const stepDesc = computed<Record<number, string>>(() => {
  const snap = regression.snapshot.value;
  return {
    1: cases.selectedIds.value.length ? `已選 ${cases.selectedIds.value.length}` : '尚未勾選',
    2: revise.revisedPrompt.value
      ? '已產生候選版'
      : revise.patches.value.length
        ? `${revise.patches.value.length} 條補丁待套用`
        : '未開始',
    3: stale.value
      ? '結果已失效'
      : snap?.status === 'done'
        ? `修好 ${snap.fixed} · 改壞 ${snap.broken}`
        : '未驗證',
    4: savedDraft.value ? `草稿 ${savedDraft.value}` : '未定案',
  };
});

/** 每一步被擋住的原因（空＝可進入）。 */
const blockedReasons = computed<Record<number, string>>(() =>
  Object.fromEntries(STEPS.map((s) => [s.no, stepBlockedReason(s.no, gateState.value)])),
);

/** 允許回退、不允許跳過：已達成前置條件的步號才可點。 */
function goStep(next: number): void {
  if (blockedReasons.value[next]) return;
  step.value = next as PipelineStep;
}

watch(step, (v) => emit('stepChange', v));

/**
 * 回退即失效。
 *
 * 候選 Prompt 變了、或案例集合真的增刪了，就把回歸結果作廢並把④鎖回去。用集合比對而非直接
 * watch 陣列，是為了讓「回①只是回頭看一眼勾了哪些」不會誤清辛苦跑出來的結果。
 */
watch(stale, (isStale) => {
  if (!isStale) return;
  // ⚠️ 刻意**不呼叫 `regression.reset()`**：清掉快照的話，③ 連「你剛才那份結果是針對別的候選版跑的」
  // 都無從顯示（整張進度卡是 `v-if="snap"`），使用者只會看到數字憑空消失。閘門靠 `stale` 就已經
  // 擋住④，舊數字留著當對照更有用；使用者按「開始回歸」時 `start()` 內部自會 reset。
  savedDraft.value = ''; // 已存的草稿對應的是舊候選版，不能拿去升版
});

/**
 * 當前步驟不得停留在已被擋住的格。
 *
 * 通用夾取而非只特判④：在②重跑一次改寫會清掉候選版（`run()` 內含 `reset()`），此時③也失去前提；
 * 只處理④的話，使用者會停在一個「進得去卻按不動」的步驟上。
 */
watch(gateState, (s) => {
  const ceiling = highestReachableStep(s);
  if (step.value > ceiling) step.value = ceiling;
});

/** 由③觸發：記下「驗的是哪份全文、哪組案例」再跑，供 stale 判定。 */
function runRegression(targetPrompt: string, overrides: LlmOverrides): void {
  validatedAt.value = {
    validatedPrompt: targetPrompt,
    validatedIds: [...cases.selectedIds.value],
  };
  void regression.start(cases.selectedIds.value, targetPrompt, overrides);
}

/** 由④觸發：存草稿並記下草稿名（升版要拿它當來源）。 */
async function onSaveDraft(): Promise<void> {
  const saved: PromptVersionSaved | null = await revise.saveVersion();
  if (!saved) return;
  // created=false（內容與最新草稿逐字相同）不是失敗——那支既有草稿一樣可以升版
  savedDraft.value = saved.version;
  emit('savedVersion');
}

function onPromoted(): void {
  emit('savedVersion');
  emit('update:visible', false);
}

// ── 開啟時載入案例 + 還原步驟 ─────────────────────────────────────────────────
watch(
  () => props.visible,
  async (open) => {
    if (!open) return;
    await cases.load();
    emit('count', cases.cases.value.length);
    if (props.preselectId && !cases.selectedIds.value.includes(props.preselectId)) {
      cases.selectedIds.value = [...cases.selectedIds.value, props.preselectId];
    }
    // clamp：query 帶來的步驟在重整後多半不成立（案例勾選與候選版都沒持久化），
    // 落到當前實際可達的最高步才是誠實行為
    step.value = clampStep(props.initialStep ?? 1, gateState.value);
  },
  { immediate: true },
);

// ── 案例列表 ────────────────────────────────────────────────────────────────
// 對話原文與模型不列欄：前者在 175px 欄寬下只看得到開頭幾個字，等於白佔版位，改放展開列；
// 列上留「標錯了哪幾欄 + 人寫的建議」——這兩項才是一眼判斷「要不要勾這則餵給 AI」的依據。
const COLUMNS = [
  { title: '評判時間', dataIndex: 'created_at', slotName: 'created', width: 150 },
  { title: '標錯', slotName: 'bad', width: 78, align: 'center' as const },
  { title: '判錯的欄位', slotName: 'badFields', width: 240, ellipsis: true, tooltip: true },
  { title: '修改建議', slotName: 'comment', minWidth: 220, ellipsis: true, tooltip: true },
  { title: 'Prompt 版本', slotName: 'version', width: 160 },
  { title: '操作', slotName: 'actions', width: 76, align: 'center' as const },
];

/** 逐欄攤開「AI 判的 → 正解」，供展開列顯示。 */
function correctionRows(row: {
  ai_output: Record<string, unknown>;
  corrections: Record<string, unknown>;
}): Array<{ key: string; before: string; after: string }> {
  return Object.entries(row.corrections ?? {}).map(([key, after]) => ({
    key,
    before: display(row.ai_output?.[key]),
    after: display(after),
  }));
}

function display(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—';
  if (typeof value === 'boolean') return value ? 'TRUE' : 'FALSE';
  if (Array.isArray(value)) return value.length ? value.join('、') : '[]（空）';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

function confirmRemove(id: number): void {
  Modal.confirm({
    title: '刪除案例',
    content: '刪掉後這則案例不再參與 AI 改寫與回歸重跑，且無法復原。',
    okText: '刪除',
    cancelText: '取消',
    okButtonProps: { status: 'danger' },
    onOk: async () => {
      await cases.remove(id);
      emit('count', cases.cases.value.length);
    },
  });
}
</script>

<template>
  <a-drawer
    :visible="visible"
    :width="1240"
    :footer="false"
    unmount-on-close
    :body-style="{ display: 'flex', flexDirection: 'column', overflow: 'hidden' }"
    @cancel="emit('update:visible', false)"
  >
    <template #title>
      <div class="flex items-center gap-2">
        <span>案例庫 × AI 改寫</span>
        <a-tag size="small">{{ cases.cases.value.length }} 則</a-tag>
        <a-tag v-if="cases.selectedIds.value.length" color="arcoblue" size="small">
          已勾 {{ cases.selectedIds.value.length }}
        </a-tag>
      </div>
    </template>

    <a-alert v-if="promptEdited" type="warning" class="mb-3">
      頁面上的 Prompt 已編輯未存檔（線上正式版為
      {{ promptVersion || '—' }}）。案例記的是評判當下的版本，改寫與回歸會以你現在編輯中的內容為基準。
    </a-alert>

    <!-- 流水線導引：唯一的導覽（不再與 tab 列並存）。未達前置條件的步號不可點，hover 說明原因 -->
    <!-- `changeable` 是 Arco 讓步號可點的開關（預設 false，不開的話 @change 根本不會觸發） -->
    <a-steps :current="step" type="navigation" small changeable class="mb-2 shrink-0" @change="goStep">
      <a-step
        v-for="s in STEPS"
        :key="s.no"
        :title="s.title"
        :description="stepDesc[s.no]"
        :disabled="!!blockedReasons[s.no]"
        :status="s.no === 3 && stale ? 'error' : undefined"
      />
    </a-steps>
    <div v-if="blockedReasons[Math.min(4, step + 1)]" class="mb-2 shrink-0 text-xs text-[#86909c]">
      下一步：{{ blockedReasons[Math.min(4, step + 1)] }}
    </div>

    <!-- 唯一的捲動容器；各步驟用 v-show 保留掛載（切步驟不重跑串流、不丟填到一半的表單） -->
    <div class="min-h-0 flex-1 overflow-auto">
      <!-- ① 選案例 -->
      <div v-show="step === 1" class="flex h-full flex-col">
        <TableLayout
          full-height
          :data="cases.cases.value"
          :columns="COLUMNS"
          :loading="cases.loading.value"
          :error="cases.error.value"
          row-key="id"
          empty-text="還沒有案例。在調試台跑一條對話，於結果卡旁逐欄標對錯後按「存為案例」。"
          :pagination="false"
          :row-selection="{
            type: 'checkbox',
            showCheckedAll: true,
            selectedRowKeys: cases.selectedIds.value,
          }"
          :expandable="{ width: 40 }"
          @selection-change="(keys: number[]) => (cases.selectedIds.value = keys)"
        >
          <template #created="{ record }">
            <span class="text-xs">{{ fmtDt(record.created_at) }}</span>
          </template>

          <template #bad="{ record }">
            <a-tag v-if="cases.badCount(record)" color="red" size="small">
              {{ cases.badCount(record) }} 欄
            </a-tag>
            <a-tag v-else color="green" size="small">全對</a-tag>
          </template>

          <template #badFields="{ record }">
            <span v-if="cases.badCount(record)" class="text-xs text-[#4e5969]">
              {{ Object.keys(record.corrections).join('、') }}
            </span>
            <span v-else class="text-xs text-[#c9cdd4]">—</span>
          </template>

          <template #comment="{ record }">
            <span v-if="record.comment" class="text-xs">{{ record.comment }}</span>
            <span v-else class="text-xs text-[#c9cdd4]">—</span>
          </template>

          <template #version="{ record }">
            <a-tag v-if="record.prompt_version" size="small">{{ record.prompt_version }}</a-tag>
            <a-tooltip v-else content="評判當下頁面上的 Prompt 是臨時編輯過的，不對應任何存檔版本">
              <a-tag color="orange" size="small">臨時編輯</a-tag>
            </a-tooltip>
          </template>

          <template #actions="{ record }">
            <a-button
              type="text"
              size="mini"
              status="danger"
              :disabled="!canManage"
              @click="confirmRemove(record.id)"
              >刪除</a-button
            >
          </template>

          <template #expand-row="{ record }">
            <div class="px-4 py-3">
              <div v-if="cases.badCount(record)" class="mb-3">
                <div class="mb-1 text-xs font-semibold text-[#1d2129]">逐欄正解</div>
                <div class="flex flex-col gap-1">
                  <div
                    v-for="item in correctionRows(record)"
                    :key="item.key"
                    class="flex flex-wrap items-baseline gap-2 text-xs"
                  >
                    <span class="min-w-[168px] font-medium text-[#4e5969]">{{ item.key }}</span>
                    <span class="text-[#f53f3f] line-through">{{ item.before }}</span>
                    <span class="text-[#86909c]">→</span>
                    <span class="font-medium text-[#00b42a]">{{ item.after }}</span>
                  </div>
                </div>
              </div>
              <div v-else class="mb-3 text-xs text-[#86909c]">
                全欄皆判對——這則是回歸正例，用來確認之後改 Prompt 沒把對的改壞。
              </div>

              <div class="mb-1 flex flex-wrap items-baseline gap-2">
                <span class="text-xs font-semibold text-[#1d2129]">對話原文（前 200 字）</span>
                <span class="text-[11px] text-[#86909c]">
                  共 {{ (record.conversation_chars ?? 0).toLocaleString() }} 字 · 判定模型
                  {{ record.model || '—' }}
                </span>
              </div>
              <pre class="conversation-preview">{{ record.conversation_preview }}</pre>
            </div>
          </template>
        </TableLayout>
      </div>

      <!-- ② AI 改寫 -->
      <div v-show="step === 2" class="p-1">
        <PromptRevisePanel
          :revise="revise"
          :system-prompt="systemPrompt"
          :review-ids="cases.selectedIds.value"
        />
      </div>

      <!-- ③ 回歸驗證 -->
      <div v-show="step === 3" class="p-1">
        <PromptRegressionPanel
          :regression="regression"
          :baseline-prompt="systemPrompt"
          :candidate-prompt="revise.revisedPrompt.value"
          :review-ids="cases.selectedIds.value"
          :drafts="drafts"
          :releases="releases"
          :stale="stale"
          @run="runRegression"
          @back-to-patches="goStep(2)"
          @request-batch="emit('requestBatch')"
        />
      </div>

      <!-- ④ 定案發布 -->
      <div v-show="step === 4" class="p-1">
        <PromptReleaseStep
          :candidate-prompt="revise.revisedPrompt.value"
          :release-prompt="releasePrompt"
          :active-release="promptVersion"
          :releases="releases"
          :broken-count="regression.snapshot.value?.broken ?? 0"
          :fixed-count="regression.snapshot.value?.fixed ?? 0"
          :saved-draft="savedDraft"
          :saving="revise.savingVersion.value"
          @save-draft="onSaveDraft"
          @promoted="onPromoted"
        />
      </div>
    </div>
  </a-drawer>
</template>

<style scoped>
.conversation-preview {
  max-height: 180px;
  overflow: auto;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  border: 1px solid #e5e6eb;
  border-radius: 6px;
  background: #f7f8fa;
  padding: 8px 10px;
  color: #4e5969;
  font-size: 11px;
  line-height: 1.6;
}
</style>

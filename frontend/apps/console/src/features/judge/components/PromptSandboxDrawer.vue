<script setup lang="ts">
/**
 * 歸因列表「Prompt 測試」沙盒抽屜：對單列或批量（依條件目標選取，含「已選內」子模式涵蓋勾選
 * 測試），跑使用者勾選的 7 條
 * prompt 子集（polarity + C-1..C-6）→ 逐筆逐 prompt 結果。**ungated**（不受正式歸因閘門限制，
 * 即使整體判正向也能測域 prompt）；測試歷史與正式初判完全分離（獨立 `prompt_sandbox_runs` 表），
 * 且捕捉完整 LLM log 供即時觀看與歷史回看（見 `sandbox_classify`/`prompt_sandbox.py`）——「執行
 * 日誌」分頁在測試跑動時走 SSE 即時串流，完成後改顯示落庫的權威快照；查看歷史紀錄時同一分頁
 * 顯示當時的完整 log，複用 `PrejudgeLogView`（與 `PrejudgeLogDrawer` 共用同一份渲染）。
 *
 * scope='all'（工具列「依條件批量」入口）時的目標選取比照初判分類（`usePrejudgeJob`），委派
 * `usePromptSandboxTargets` 復用同一套 stage 驅動 + 篩選草稿 + 即時筆數預覽 pattern。
 *
 * 所有 Prompt 測試都在此抽屜進行。草稿閉環（編輯 → 測試 → 對比 → 入庫）：版本選擇
 * （PromptVersionPickerGroup withDrafts）可對每支 prompt 編輯 DB 草稿並以「📝 草稿」選項送測；
 * 有草稿時預設同 job 雙跑對比（baseline vs draft，token ×2），結果並排差異高亮＋等價性
 * metrics；滿意後「採納草稿入庫」（PromptDraftAdoptDrawer：diff 確認 → saveRule 即 active →
 * 清草稿）。測試歷史另支援勾兩筆 run-vs-run 對比（同一套對比視圖）。
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { Message } from '@arco-design/web-vue';
import { PERM } from '@/api';
import { usePermission } from '@/composables/usePermission';
import { useJudgeRulesStore } from '@/stores/judgeRules.store';
import { differs, fmtDt, metricRows } from '../utils';
import type { ProblemRow } from '../constants/source-schema.constant';
import type { CascadeNode } from '@/api';
import { CollapsibleSidePanel, LlmConfigPicker, LlmKnobs, StickyTabs, TableLayout } from '@/components';
// 相對路徑 import（非走 barrel）：本檔自身即為 components barrel 的一員，經 barrel 迴繞 import
// 同資料夾元件會觸發 circular dep（見 barrel-exports 規則）。
import AttributionFilterBar from './AttributionFilterBar.vue';
import PrejudgeLogView from './PrejudgeLogView.vue';
import PromptDraftAdoptDrawer from './PromptDraftAdoptDrawer.vue';
import PromptDraftEditorDrawer from './PromptDraftEditorDrawer.vue';
import PromptVersionPickerGroup from './PromptVersionPickerGroup.vue';
import SandboxCompareCard from './SandboxCompareCard.vue';
import SandboxPromptEntries from './SandboxPromptEntries.vue';
import { idPlaceholderFor, schemaFor, STAGE_LABELS, type FilterField } from '../constants';
import { useLlmAreaDefault } from '../composables/useLlmAreaDefault';
import { usePromptSandboxDrafts } from '../composables/usePromptSandboxDrafts';
import {
  SANDBOX_SCOPE_LABEL as SCOPE_LABEL,
  usePromptSandboxHistory,
} from '../composables/usePromptSandboxHistory';
import { usePromptSandboxJob } from '../composables/usePromptSandboxJob';
import { usePromptSandboxTargets } from '../composables/usePromptSandboxTargets';
import type { PrejudgeListFilters } from '../composables/usePrejudgeJob';

const props = defineProps<{
  /** 是否顯示。 */
  visible: boolean;
  /** 當前反饋來源 code（product_reviews…）。 */
  source: string;
  /** 觸發入口：single＝單列按鈕；all＝工具列批量（內建依條件目標選取，含「已選內」子模式）。 */
  scope: 'single' | 'all';
  /** 受測 source_id 清單（僅 single 時顯式帶入；all 時一律由內部依條件解析，此 prop 忽略）。 */
  sourceIds: string[];
  /** scope=single 時的目標列（供顯示評論原文預覽）。 */
  row?: ProblemRow | null;
  /** scope='all' 目標選取依賴：生效商品垂直分類。 */
  effVerticals?: string[];
  /** scope='all' 目標選取依賴：跨頁累積勾選（targetMode='selected' 時 within_ids 交集）。 */
  selectedKeys?: string[];
  /** scope='all' 目標選取依賴：頁面當前列表篩選快照（開選取器自動帶入草稿初值）。 */
  listFilters?: PrejudgeListFilters;
  /** scope='all' 目標篩選欄的歸因分類級聯選項（taxonomy 欄必給，來自 getTaxonomyCascade）。 */
  cascadeOptions?: CascadeNode[];
}>();

const emit = defineEmits<{
  (e: 'update:visible', v: boolean): void;
}>();

// 7 支 prompt 的選版本/開關/草稿模式下沉進 PromptVersionPickerGroup；store 供 labelFor（草稿
// 編輯/採納抽屜標題）與 active 版本（stale 提示）。
const rulesStore = useJudgeRulesStore();
const selectedCodes = ref<string[]>([]);
const llm = useLlmAreaDefault('sandbox');
const { can } = usePermission();
const versionSelection = ref<{ versions: Record<string, number> }>({ versions: {} });
/** rule_code（prompt_C-3）→ 端點值（C-3 / polarity）。 */
const toPromptArg = (code: string): string => code.replace('prompt_', '');
const promptArgs = computed(() => selectedCodes.value.map(toPromptArg));

/** 把目前 provider + 旋鈕存為 sandbox 功能區默認（team 共用）。 */
const onSaveLlmAreaDefault = async () => {
  try {
    await llm.saveAsDefault();
    Message.success('已存為本功能區默認');
  } catch (e: any) {
    Message.error('儲存失敗：' + (e?.message || e));
  }
};

// ── 草稿閉環：納入測試的 rule_code / 雙跑對比開關（PromptVersionPickerGroup 直接 emit 給本檔，
// 與 selectedCodes/versionSelection 同源；抽屜協調邏輯下沉 usePromptSandboxDrafts）──
/** 納入測試且處於草稿模式的 rule_code（picker emit；送測時逐條取 DB 草稿內容快照）。 */
const draftCodes = ref<string[]>([]);
/** 有草稿時是否雙跑對比（預設開；關＝只跑草稿省 token 但無前後對照）。 */
const compareEnabled = ref(true);

const activeTab = ref<'results' | 'log' | 'history'>('results');
/** 初判設定面板是否展開；預設收合，讓執行日誌/測試結果直接可見，不被設定區擠到下面。 */
const settingsOpen = ref(false);

// ── scope='all' 依條件批量選取（比照初判分類 usePrejudgeJob 的目標選取 pattern）──
const targets = usePromptSandboxTargets({
  source: () => props.source,
  effVerticals: () => props.effVerticals,
  selectedKeys: () => props.selectedKeys ?? [],
  listFilters: () => props.listFilters ?? {},
});
/** 依條件批量選取的目標篩選欄位子集（順序對齊初判分類 PREJUDGE_TARGET_FIELDS；
 * 初判階段由上方 checkbox 承擔，不納入此欄）。 */
const TARGET_FIELDS: FilterField[] = [
  'recOid',
  'prodOid',
  'orderOid',
  'dateRange',
  'polarity',
  'tier',
  'taxonomy',
  'hasExternal',
];
/** 精確查詢 placeholder（隨來源：評論 rec_oid／進線 session_oid…）。 */
const idPlaceholder = computed(() => idPlaceholderFor(props.source));
/** 原文預覽標籤（隨來源：反饋內容／進線對話…）。 */
const contentLabel = computed(() => schemaFor(props.source).contentLabel);
/** 任一目標選取條件變更（範圍/階段/篩選欄）→ 重新預覽「將測試 N 筆」。 */
const onTargetChange = () => {
  if (props.scope === 'all' && props.visible) void targets.refreshTargetCount(promptArgs.value);
};
watch(promptArgs, onTargetChange);

// ── 測試歷史（與正式初判歷史完全分離）──
const {
  history,
  historyLoading,
  loadHistory,
  compareSelection,
  comparing,
  viewHistoryRun: fetchHistoryRunDetail,
  doCompareRuns: requestRunCompare,
} = usePromptSandboxHistory();

// ── 沙盒執行（啟動測試 + SSE 執行日誌 + 輪詢至終態 + 當前顯示結果）──
const { running, activeRun, runCompare, logEntries, logStreaming, run, closeLogStream, invalidate } =
  usePromptSandboxJob({
    scope: () => props.scope,
    source: () => props.source,
    sourceIds: () => props.sourceIds,
    promptArgs,
    versionSelection,
    draftCodes,
    compareEnabled,
    overrides: llm.overrides,
    scopeBody: targets.scopeBody,
    labelFor: (code: string): string => rulesStore.labelFor(code),
    settingsOpen,
    activeTab,
    onDone: loadHistory,
  });

// ── 草稿閉環（編輯 → 測試 → 對比 → 入庫）：草稿編輯 / 採納入庫抽屜協調 ──
const { pickerRef, draftEditor, adopt, openDraftEditor, onDraftChanged, onAdopted, openAdopt } =
  usePromptSandboxDrafts({ activeRun });

/** 雙跑 run 的「結果有差異」筆數（對比頭部摘要）。 */
const changedCount = computed(() => {
  const rs = activeRun.value?.results ?? [];
  return rs.filter((r) => r.compare && differs(r.baseline, r.draft)).length;
});
/** 當前 run 帶的草稿快照 code 清單（採納入庫動作列；不依賴 compare——只跑草稿亦可採納）。 */
const runDraftCodes = computed(() => Object.keys(activeRun.value?.drafts ?? {}));

onBeforeUnmount(() => {
  invalidate(); // 作廢進行中的輪詢迴圈（見 usePromptSandboxJob 的 token 機制）
  closeLogStream();
});

/** 反饋原文預覽（僅單列有意義）：標題另有獨立區塊顯示（見 reviewTitle），這裡只放內文，
 * 兩者皆可能為空（如純星等無文字的評論）。 */
const reviewText = computed(() => String(props.row?.content ?? ''));
const reviewTitle = computed(() => String(props.row?.title ?? ''));

/** 範圍摘要文字（依 scope 顯示不同語意）。 */
const scopeSummary = computed(() => {
  if (props.scope === 'single') return '單列測試';
  return `批量 · 將測試 ${targets.targetCount.value} 筆`;
});

/** 查看某次歷史測試：拉完整詳情（含 results + log 快照）並切到結果分頁；log 分頁同步顯示
 * 當時的完整快照（靜態，非串流）——需求「透過測試歷史回看當時的完整 log」的落地點。 */
async function viewHistoryRun(runId: string): Promise<void> {
  closeLogStream(); // 回看歷史時若有正在跑的即時串流先關閉，避免與靜態快照混淆
  runCompare.value = null; // 離開 run-vs-run 檢視
  await fetchHistoryRunDetail(runId, (detail) => {
    activeRun.value = detail;
    logEntries.value = detail.log;
    activeTab.value = 'results';
  });
}

/** 測試歷史勾恰兩筆 → run-vs-run 對比（後端按 source_id 對齊 + metrics），結果分頁顯示。 */
async function doCompareRuns(): Promise<void> {
  await requestRunCompare((compare) => {
    runCompare.value = compare;
    activeTab.value = 'results';
  });
}

// 開啟時重置狀態 + 載入歷史（選哪些 prompt 由 PromptVersionPickerGroup 的開關預設，見
// usePromptVersionPicker：預設僅 polarity 開，免每次手動勾）；scope='all' 時初始化目標選取器。
watch(
  () => props.visible,
  async (v) => {
    if (!v) {
      invalidate(); // 作廢進行中的輪詢迴圈：關抽屜後不再打 API、不覆寫重開後的畫面
      running.value = false; // 被作廢的迴圈不會再動 running（token 已過期），這裡顯式復位
      activeRun.value = null;
      runCompare.value = null;
      compareSelection.value = [];
      closeLogStream();
      logEntries.value = [];
      return;
    }
    activeTab.value = 'results';
    settingsOpen.value = false;
    void llm.loadConfigs();
    if (props.scope === 'all') {
      targets.openTargetPicker();
      void targets.refreshTargetCount(promptArgs.value);
    }
    await loadHistory();
    // 免手動點「測試歷史」分頁才看得到最新一次結果：開啟時自動帶入最近一筆（歷史為全域列表，
    // 依 created_at 降冪，故 [0] 即最新）。
    if (!activeRun.value && history.value.length) {
      await viewHistoryRun(history.value[0].run_id);
    }
  },
);
</script>

<template>
  <a-drawer
    :visible="visible"
    title="Prompt 測試（沙盒 · 不受正式歸因閘門限制 · 不落正式初判）"
    :width="scope === 'all' ? 1080 : 960"
    :footer="false"
    :body-style="{
      display: 'flex',
      flexDirection: 'column',
      overflow: 'hidden',
    }"
    @cancel="emit('update:visible', false)"
  >
    <div class="mb-2 text-xs text-[var(--color-text-3)]">
      {{ scopeSummary }} · 勾選要測試的 prompt，即使整體判正向也照跑（不受正式閘門限制）
    </div>

    <!-- 左側收合軌 + 右側主內容：初判設定（模型/版本，scope='all' 再加目標範圍）預設收合，
         點窄直排 tab 展開/收合，收合時執行日誌／測試結果直接可見，不被設定區擠到下面。
         面板用 v-show（非 v-if）保持掛載：即使收合，PromptVersionPickerGroup 的預設勾選仍會
         立即生效，避免「確認」按鈕因元件未掛載而誤判為未選任何 prompt。 -->
    <div class="flex min-h-0 flex-1 gap-3 overflow-hidden">
      <CollapsibleSidePanel
        v-model="settingsOpen"
        label="初判設定"
        floating
        panel-class="w-[560px]"
      >
        <!-- 一頁化：目標範圍（scope='all' 專屬）＋初判設定順排全展開，無內層頁籤——開面板即見
             全部配置。目標範圍比照初判分類目標選取；adhoc＝臨時貼 ID。 -->
        <div v-if="scope === 'all'">
          <a-divider orientation="left" :margin="12">目標範圍</a-divider>
          <div class="mb-2 flex items-center gap-3">
            <span class="text-xs text-[var(--color-text-3)]">範圍</span>
            <a-radio-group
              v-model="targets.targetMode.value"
              type="button"
              size="small"
              @change="onTargetChange"
            >
              <a-radio value="adhoc">臨時貼 ID</a-radio>
              <a-radio value="scope">全部資料</a-radio>
              <a-radio value="selected" :disabled="!selectedKeys?.length">已選內</a-radio>
            </a-radio-group>
          </div>

          <!-- 臨時貼 ID：換行分隔 -->
          <div v-if="targets.targetMode.value === 'adhoc'" class="mb-2">
            <a-textarea
              v-model="targets.adhocText.value"
              :auto-size="{ minRows: 3, maxRows: 6 }"
              placeholder="每行一個 source_id，貼上後自動去重"
              @input="onTargetChange"
            />
          </div>

          <template
            v-if="targets.targetMode.value === 'scope' || targets.targetMode.value === 'selected'"
          >
            <div class="mb-2">
              <div class="mb-1 text-xs text-[var(--color-text-3)]">
                目標初判階段（預設只測未初判）
              </div>
              <a-checkbox-group v-model="targets.targetStages.value" @change="onTargetChange">
                <a-checkbox v-for="(lbl, code) in STAGE_LABELS" :key="code" :value="code">{{
                  lbl
                }}</a-checkbox>
              </a-checkbox-group>
            </div>
            <div class="mb-1 text-xs text-[var(--color-text-3)]">
              目標篩選（已自動帶入列表當前篩選，可重選）
            </div>
            <AttributionFilterBar
              :model="targets.draftFilters"
              :fields="TARGET_FIELDS"
              :cascade-options="cascadeOptions"
              :id-placeholder="idPlaceholder"
              @change="onTargetChange"
            />
            <div class="mt-1 text-xs text-[var(--color-text-3)]">
              日期 / ID / 外部評論 對所有目標生效；傾向 / 信心分層 / 歸因分類
              僅對「已初判」階段生效。
            </div>
          </template>
          <div class="mt-2 text-xs text-[var(--color-text-3)]">
            將測試 {{ targets.targetCount.value }} 筆
          </div>
        </div>

        <!-- 初判設定（恆顯示）：模型 + 開關控制是否納入本次測試（每支預設沿用 active，可個別切換歷史版本）。 -->
        <div>
          <a-divider orientation="left" :margin="12">初判設定</a-divider>
          <a-alert v-if="!Object.keys(llm.providerHasToken.value).length" type="warning" class="mb-2">
            尚無可用 LLM 連線，請先至「設定 › LLM 連線」建立並保存 API Token。
          </a-alert>
          <LlmConfigPicker
            :model-value="llm.provider.value"
            :provider-has-token="llm.providerHasToken.value"
            class="mb-2"
            @update:model-value="llm.setProvider"
          />
          <LlmKnobs
            :model-value="llm.knobs"
            :provider="llm.provider.value"
            class="mb-2"
            @update:model-value="llm.setKnobs"
          />
          <div class="mb-2 flex justify-end">
            <a-button
              size="small"
              :disabled="!can(PERM.settingsLlmAreaDefaultWrite)"
              @click="onSaveLlmAreaDefault"
              >存為此區默認</a-button
            >
          </div>
          <div class="mb-1 text-xs text-[var(--color-text-3)]">
            Prompt 版本（開關控制是否納入本次測試；每支預設沿用 active，可切歷史版本或 📝
            草稿；編輯鈕可即時修改草稿）
          </div>
          <PromptVersionPickerGroup
            ref="pickerRef"
            :with-toggle="true"
            :with-drafts="true"
            @update:resolved="(v) => (versionSelection = v)"
            @update:enabled-codes="(codes) => (selectedCodes = codes)"
            @update:draft-codes="(codes) => (draftCodes = codes)"
            @edit-draft="openDraftEditor"
          />
          <div
            v-if="draftCodes.length"
            class="mt-2 flex items-center gap-2 rounded border border-dashed border-[var(--color-border-3)] px-2 py-1.5 text-xs"
          >
            <a-switch v-model="compareEnabled" size="small" />
            <span
              >與基準雙跑對比：同批 item 以「選定版本」與「草稿」各跑一遍並排差異（<span
                class="text-[rgb(var(--orange-6))]"
                >token 成本 ×2</span
              >）；關閉＝只跑草稿</span
            >
          </div>
          <div class="mt-3 text-xs text-[var(--color-text-3)]">
            確認後開始測試，過程會消耗 token（不落正式初判、不受正式閘門限制）。
          </div>
        </div>

        <!-- 動作列收在面板內（面板＝設定表單）：取消＝收合面板；確認＝執行測試並自動收合。 -->
        <template #footer>
          <a-button size="small" @click="settingsOpen = false">取消</a-button>
          <a-button
            type="primary"
            size="small"
            :loading="running"
            :disabled="!selectedCodes.length || (scope === 'all' && !targets.targetCount.value)"
            @click="run"
          >
            確認
          </a-button>
        </template>
      </CollapsibleSidePanel>

      <!-- 主內容：原文預覽 + 結果/日誌/歷史三分頁，緊貼在收合軌右側 -->
      <div class="flex min-w-0 flex-1 flex-col overflow-hidden">
        <!-- 原文預覽（僅單列有意義；標籤隨來源：反饋內容／進線對話…；標題+內容一併顯示，
             避免有標題的來源只看得到內文看不到標題） -->
        <div
          v-if="scope === 'single' && (reviewTitle || reviewText)"
          class="mb-3 flex-none rounded-lg border bg-[var(--color-fill-1)] p-3"
        >
          <div class="mb-1 text-xs font-medium text-[var(--color-text-3)]">{{ contentLabel }}</div>
          <div v-if="reviewTitle" class="mb-1 text-sm font-medium text-[var(--color-text-1)]">
            {{ reviewTitle }}
          </div>
          <div class="whitespace-pre-wrap text-sm leading-relaxed">
            {{ reviewText }}
          </div>
        </div>

        <!-- Tab 列固定可見、僅內容捲動（見 .claude/rules/frontend-vue.md Tabs 規則）：公共元件取代裸 a-tabs -->
        <StickyTabs v-model:active-key="activeTab" class="min-h-0 flex-1">
          <a-tab-pane key="results" title="測試結果">
            <div class="h-full overflow-auto">
              <a-spin v-if="running" class="block py-8 text-center" />

              <!-- run-vs-run 對比檢視（測試歷史勾兩筆） -->
              <template v-else-if="runCompare">
                <div class="mb-2 flex items-center gap-2 text-xs text-[var(--color-text-3)]">
                  <a-button size="mini" type="text" @click="runCompare = null">← 返回</a-button>
                  <span>
                    A：{{ fmtDt(runCompare.a.created_at) }} · {{ runCompare.a.model }}
                    <span class="mx-1 text-[var(--color-text-4)]">vs</span>
                    B：{{ fmtDt(runCompare.b.created_at) }} · {{ runCompare.b.model }}
                  </span>
                </div>
                <div
                  v-if="runCompare.metrics"
                  class="mb-3 flex flex-wrap items-center gap-x-4 gap-y-1 rounded border bg-[var(--color-fill-1)] px-3 py-2 text-xs"
                >
                  <span class="text-[var(--color-text-3)]">對齊 {{ runCompare.metrics.n }} 筆</span>
                  <span v-for="m in metricRows(runCompare.metrics)" :key="m.label">
                    {{ m.label }} <span class="font-mono font-medium">{{ m.value }}</span>
                  </span>
                </div>
                <div class="flex flex-col gap-3">
                  <SandboxCompareCard
                    v-for="item in runCompare.items"
                    :key="item.source_id"
                    :source-id="item.source_id"
                    :has-diff="differs(item.a, item.b)"
                    left-label="A"
                    right-label="B"
                    :left="item.a"
                    :right="item.b"
                  />
                </div>
              </template>

              <template v-else-if="activeRun">
                <div class="mb-2 flex items-center gap-2 text-xs text-[var(--color-text-3)]">
                  <span>
                    {{ fmtDt(activeRun.created_at) }} · model={{ activeRun.model }} ·
                    {{ activeRun.item_count }} 筆
                  </span>
                  <a-tag v-if="activeRun.compare" size="small" color="purple">草稿雙跑對比</a-tag>
                  <!-- 只跑草稿（關閉對比）的 run：結果內容來自草稿，必須可分辨（否則與一般
                       選版本測試外觀完全相同，使用者無從判斷這是草稿驗證結果） -->
                  <a-tag v-else-if="runDraftCodes.length" size="small" color="purple"
                    >草稿結果（未對比）</a-tag
                  >
                </div>

                <!-- 雙跑對比 run：差異摘要 + metrics -->
                <div
                  v-if="activeRun.compare"
                  class="mb-2 flex flex-wrap items-center gap-x-4 gap-y-1 rounded border bg-[var(--color-fill-1)] px-3 py-2 text-xs"
                >
                  <span class="text-[var(--color-text-3)]"
                    >{{ activeRun.results.length }} 筆中
                    <span class="font-medium text-[rgb(var(--orange-6))]">{{ changedCount }}</span>
                    筆結果有差異</span
                  >
                  <span v-for="m in metricRows(activeRun.metrics)" :key="m.label">
                    {{ m.label }} <span class="font-mono font-medium">{{ m.value }}</span>
                  </span>
                </div>
                <!-- 採納入庫動作列：只要本次 run 帶了草稿快照就提供（不依賴 compare——關閉
                     雙跑對比「只跑草稿」仍是合法工作流程，閉環最後一步不可消失） -->
                <div
                  v-if="runDraftCodes.length"
                  class="mb-3 flex flex-wrap items-center gap-2 text-xs"
                >
                  <span class="text-[var(--color-text-3)]">滿意草稿結果？</span>
                  <a-button
                    v-for="code in runDraftCodes"
                    :key="code"
                    type="primary"
                    size="mini"
                    @click="openAdopt(code)"
                    >採納「{{ rulesStore.labelFor(code) }}」草稿入庫</a-button
                  >
                </div>

                <div class="flex flex-col gap-3">
                  <template v-for="item in activeRun.results" :key="item.source_id">
                    <!-- 雙跑對比 item：左基準 / 右草稿並排（與 run-vs-run 共用同一卡片佈局） -->
                    <SandboxCompareCard
                      v-if="item.compare && !item.error"
                      :source-id="item.source_id"
                      :has-diff="differs(item.baseline, item.draft)"
                      left-label="基準（選定版本）"
                      right-label="草稿"
                      :left="item.baseline ?? null"
                      :right="item.draft ?? null"
                    />
                    <div v-else class="rounded-lg border p-3">
                      <div class="mb-2 flex items-center gap-2">
                        <span class="font-mono text-xs text-[var(--color-text-3)]">{{
                          item.source_id
                        }}</span>
                        <a-tag v-if="!item.error && item.polarity" size="small">{{
                          item.polarity
                        }}</a-tag>
                      </div>
                      <a-alert v-if="item.error" type="error">{{ item.error }}</a-alert>
                      <SandboxPromptEntries v-else :prompts="item.prompts ?? []" />
                    </div>
                  </template>
                </div>
              </template>
              <div v-else class="py-8 text-center text-xs text-[var(--color-text-3)]">
                勾選 Prompt 後點「確認」
              </div>
            </div>
          </a-tab-pane>
          <a-tab-pane key="log">
            <template #title>
              執行日誌
              <a-tag v-if="logStreaming" size="small" color="arcoblue" class="ml-1">串流中</a-tag>
            </template>
            <!-- 捲動已下沉至 PrejudgeLogView 內部自己的 StickyTabs（見該檔）；本層禁止再疊
                 overflow-auto（frontend-vue.md StickyTabs 規則：外層疊 overflow-auto 會產生雙捲軸，
                 破壞內層 tab/側欄的固定機制），改用 overflow-hidden 讓內部機制接管。 -->
            <div class="h-full overflow-hidden">
              <PrejudgeLogView :entries="logEntries" :streaming="logStreaming" />
            </div>
          </a-tab-pane>
          <a-tab-pane key="history" title="測試歷史">
            <div class="h-full overflow-hidden">
              <TableLayout
                full-height
                :data="history"
                :loading="historyLoading"
                :pagination="false"
                row-key="run_id"
                size="mini"
                empty-text="尚無測試紀錄"
                :row-selection="{ type: 'checkbox', showCheckedAll: false }"
                :selected-keys="compareSelection"
                @selection-change="
                  (keys: (string | number)[]) => (compareSelection = keys.map(String))
                "
              >
                <template #toolbar>
                  <div class="flex items-center gap-2 text-xs">
                    <span class="text-[var(--color-text-3)]">勾選兩筆可對比結果差異</span>
                    <a-button
                      size="mini"
                      type="outline"
                      :disabled="compareSelection.length !== 2"
                      :loading="comparing"
                      @click="doCompareRuns"
                      >對比所選 2 筆</a-button
                    >
                  </div>
                </template>
                <template #columns>
                  <a-table-column title="時間" data-index="created_at" :width="150">
                    <template #cell="{ record }">{{ fmtDt(record.created_at) }}</template>
                  </a-table-column>
                  <a-table-column title="範圍" data-index="scope" :width="70">
                    <template #cell="{ record }">{{
                      SCOPE_LABEL[record.scope] ?? record.scope
                    }}</template>
                  </a-table-column>
                  <a-table-column title="筆數" data-index="item_count" :width="60" />
                  <a-table-column title="Prompt" :width="140" ellipsis tooltip>
                    <template #cell="{ record }">
                      <a-tag v-if="record.compare" size="small" color="purple" class="mr-1"
                        >對比</a-tag
                      >{{ record.prompt_ids.join('、') }}
                    </template>
                  </a-table-column>
                  <a-table-column title="模型" data-index="model" ellipsis tooltip />
                  <a-table-column title="觸發人" data-index="triggered_by" ellipsis tooltip />
                  <a-table-column title="" :width="70">
                    <template #cell="{ record }">
                      <a-button type="text" size="mini" @click="viewHistoryRun(record.run_id)"
                        >查看</a-button
                      >
                    </template>
                  </a-table-column>
                </template>
              </TableLayout>
            </div>
          </a-tab-pane>
        </StickyTabs>
      </div>
    </div>

    <!-- 草稿編輯抽屜（picker 每列編輯鈕開啟；存檔/刪除後刷新草稿選項） -->
    <PromptDraftEditorDrawer
      v-model:visible="draftEditor.visible"
      :code="draftEditor.code"
      :label="rulesStore.labelFor(draftEditor.code)"
      :base-version="draftEditor.baseVersion"
      :active-version="pickerRef?.activeVersionOf(draftEditor.code)"
      @changed="onDraftChanged"
    />
    <!-- 採納入庫確認抽屜（diff 對照 → saveRule 即 active → 清草稿） -->
    <PromptDraftAdoptDrawer
      v-model:visible="adopt.visible"
      :code="adopt.code"
      :label="rulesStore.labelFor(adopt.code)"
      :draft-text="adopt.draftText"
      :run-id="adopt.runId"
      @adopted="onAdopted"
    />
  </a-drawer>
</template>

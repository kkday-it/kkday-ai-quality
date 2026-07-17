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
import {
  comparePromptSandboxRuns,
  getPromptSandboxRun,
  getPromptSandboxStatus,
  listPromptSandboxRuns,
  prejudgeLogStreamUrl,
  startPromptSandbox,
  type PromptSandboxItemResult,
  type PromptSandboxRunCompare,
  type PromptSandboxRunSummary,
  type PromptSandboxStartBody,
  type PromptSandboxVariantResult,
  type SandboxCompareMetrics,
} from '@/api';
import { getRuleDraft } from '@/api/judgeRules.api';
import { useJudgeRulesStore } from '@/stores/judgeRules.store';
import { fmtDt } from '../utils';
import type { ProblemRow } from '../constants/source-schema.constant';
import type { CascadeNode } from '@/api';
import { CollapsibleSidePanel, StickyTabs, TableLayout } from '@/components';
// 相對路徑 import（非走 barrel）：本檔自身即為 components barrel 的一員，經 barrel 迴繞 import
// 同資料夾元件會觸發 circular dep（見 barrel-exports 規則）。
import AttributionFilterBar from './AttributionFilterBar.vue';
import LlmConfigSelect from './LlmConfigSelect.vue';
import PrejudgeLogView from './PrejudgeLogView.vue';
import PromptDraftAdoptDrawer from './PromptDraftAdoptDrawer.vue';
import PromptDraftEditorDrawer from './PromptDraftEditorDrawer.vue';
import PromptVersionPickerGroup from './PromptVersionPickerGroup.vue';
import SandboxCompareCard from './SandboxCompareCard.vue';
import SandboxPromptEntries from './SandboxPromptEntries.vue';
import type { LogEntry } from './PrejudgeLogView.types';
import { idPlaceholderFor, schemaFor, STAGE_LABELS, type FilterField } from '../constants';
import { useLlmConfigs } from '../composables/useLlmConfigs';
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
const { llmConfigId, llmConfigs } = useLlmConfigs();
const versionSelection = ref<{ versions: Record<string, number> }>({ versions: {} });
/** rule_code（prompt_C-3）→ 端點值（C-3 / polarity）。 */
const toPromptArg = (code: string): string => code.replace('prompt_', '');
const promptArgs = computed(() => selectedCodes.value.map(toPromptArg));

// ── 草稿閉環狀態 ──
/** 納入測試且處於草稿模式的 rule_code（picker emit；送測時逐條取 DB 草稿內容快照）。 */
const draftCodes = ref<string[]>([]);
/** 有草稿時是否雙跑對比（預設開；關＝只跑草稿省 token 但無前後對照）。 */
const compareEnabled = ref(true);
const pickerRef = ref<InstanceType<typeof PromptVersionPickerGroup>>();
/** 草稿編輯抽屜。 */
const draftEditor = ref<{ visible: boolean; code: string; baseVersion: number }>({
  visible: false,
  code: '',
  baseVersion: 0,
});
/** 採納入庫確認抽屜（draftText＝測試 run 的草稿快照）。 */
const adopt = ref<{ visible: boolean; code: string; draftText: string; runId: string }>({
  visible: false,
  code: '',
  draftText: '',
  runId: '',
});
/** run-vs-run 對比檢視（非 null 時結果分頁顯示對比而非單次 run）。 */
const runCompare = ref<PromptSandboxRunCompare | null>(null);
/** 測試歷史勾選（恰 2 筆可對比）。 */
const compareSelection = ref<string[]>([]);

function openDraftEditor(payload: { code: string; baseVersion: number }): void {
  draftEditor.value = { visible: true, ...payload };
}
/** 草稿存檔/刪除 → 刷新 picker 草稿選項。 */
function onDraftChanged(): void {
  void pickerRef.value?.refreshDrafts();
}
/** 入庫成功 → 新版本進下拉並選中 + 草稿選項消失。 */
function onAdopted(payload: { code: string }): void {
  void pickerRef.value?.reloadHistory(payload.code);
  void pickerRef.value?.refreshDrafts();
}
/** 從當前 run 的草稿快照發起採納。 */
function openAdopt(code: string): void {
  const text = activeRun.value?.drafts?.[code] ?? '';
  if (!text) {
    Message.warning('本次測試無此 prompt 的草稿快照');
    return;
  }
  adopt.value = { visible: true, code, draftText: text, runId: activeRun.value?.run_id ?? '' };
}

// ── 對比輔助（雙跑 item 與 run-vs-run item 共用）──
/** 兩側結果是否有實質差異（極性不同或 (prompt_id, l2_code) 集合不同）——對比卡片標記用。 */
function differs(
  a?: PromptSandboxVariantResult | PromptSandboxItemResult | null,
  b?: PromptSandboxVariantResult | PromptSandboxItemResult | null,
): boolean {
  if (!a || !b) return true;
  if (a.polarity !== b.polarity) return true;
  const key = (v: PromptSandboxVariantResult | PromptSandboxItemResult) =>
    (v.prompts ?? [])
      .flatMap((p) => (p.attributions ?? []).map((x) => `${p.prompt_id}:${x.l2_code}`))
      .sort()
      .join('|');
  return key(a) !== key(b);
}
/** 雙跑 run 的「結果有差異」筆數（對比頭部摘要）。 */
const changedCount = computed(() => {
  const rs = activeRun.value?.results ?? [];
  return rs.filter((r) => r.compare && differs(r.baseline, r.draft)).length;
});
/** 當前 run 帶的草稿快照 code 清單（採納入庫動作列；不依賴 compare——只跑草稿亦可採納）。 */
const runDraftCodes = computed(() => Object.keys(activeRun.value?.drafts ?? {}));
/** metrics 顯示格式（null → —；比率 → 百分比）。 */
const pct = (v: number | null | undefined): string =>
  v == null ? '—' : `${Math.round(v * 1000) / 10}%`;
/** metrics 摘要條目（雙跑 run 與 run-vs-run 共用渲染）。 */
const metricRows = (m: SandboxCompareMetrics | null | undefined) =>
  m
    ? [
        { label: '極性一致', value: pct(m.polarity_agree) },
        { label: '情緒分一致', value: pct(m.sentiment_agree) },
        { label: '歸因 Jaccard', value: pct(m.facet_jaccard_mean) },
        { label: '主歸因一致', value: pct(m.primary_agree) },
        { label: '筆數一致', value: pct(m.count_equal) },
      ]
    : [];

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

const activeTab = ref<'results' | 'log' | 'history'>('results');
/** 左側選單目前顯示的面板；scope='single' 沒有「目標範圍」項，恆為 settings。 */
const sandboxPanel = ref<'target' | 'settings'>('settings');
/** 初判設定面板是否展開；預設收合，讓執行日誌/測試結果直接可見，不被設定區擠到下面。 */
const settingsOpen = ref(false);
const running = ref(false);
type RunDetail = PromptSandboxRunSummary & {
  results: PromptSandboxItemResult[];
  log: LogEntry[];
  /** 草稿測試 run：各 prompt 的草稿 md 全文快照（採納入庫的內容來源）。 */
  drafts?: Record<string, string>;
  /** 雙跑對比 run：baseline vs draft 等價性聚合（後端讀取時動態算）。 */
  metrics?: SandboxCompareMetrics | null;
};
const activeRun = ref<RunDetail | null>(null);
/** 防禦舊資料：歷史 run 的 log/results 可能為 null（舊 schema 落庫），v-for 迭代 null 會讓整個
 * 抽屜 render 掛掉（實測全白）——載入點統一補空陣列。 */
const _normalizeRun = (r: RunDetail): RunDetail => {
  r.results = r.results ?? [];
  r.log = r.log ?? [];
  for (const item of r.results) item.prompts = item.prompts ?? [];
  return r;
};

// ── 執行日誌（跑測試時 SSE 即時串流；完成/回看歷史時改顯示落庫的權威 log 快照）──
// 逐條渲染委派同一份 PrejudgeLogView（與 PrejudgeLogDrawer 共用同一份渲染元件，但 SSE 連線生命週期自理）。
const logEntries = ref<LogEntry[]>([]);
const logStreaming = ref(false);
let logEs: EventSource | null = null;
const _closeLogStream = () => {
  logEs?.close();
  logEs = null;
  logStreaming.value = false;
};
const _openLogStream = (jobId: string) => {
  _closeLogStream();
  logEntries.value = [];
  logStreaming.value = true;
  logEs = new EventSource(prejudgeLogStreamUrl(jobId));
  logEs.onopen = () => {
    logEntries.value = []; // 自動重連會整批重放 → 先清空避免重複
  };
  logEs.onmessage = (ev) => logEntries.value.push(JSON.parse(ev.data));
  logEs.addEventListener('done', () => _closeLogStream());
  logEs.addEventListener('error', (ev) => {
    // 僅後端明確推送的 error event（帶 data）才終止；原生連線瞬斷無 data → 交給自動重連
    // （瞬斷即關流會讓日誌永遠空白）。
    if ((ev as MessageEvent).data) _closeLogStream();
  });
};
onBeforeUnmount(() => {
  runSeq += 1; // 作廢進行中的輪詢迴圈（見 run() 的 token 機制）
  _closeLogStream();
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

// 輪詢世代 token：抽屜關閉/元件卸載/新一輪測試時遞增作廢舊迴圈——舊 run() 的輪詢在下一次
// await 醒來後發現 token 過期即靜默退出，不再打 API、不覆寫使用者當下畫面（如切去看的
// 歷史紀錄）、也不動新一輪的 running 狀態。
let runSeq = 0;
async function run() {
  if (!selectedCodes.value.length) {
    Message.warning('請至少勾選一支 Prompt');
    return;
  }
  if (props.scope === 'single' && !props.sourceIds.length) {
    Message.warning('沒有受測項目');
    return;
  }
  const token = ++runSeq;
  running.value = true;
  activeRun.value = null;
  runCompare.value = null; // 新一輪測試離開 run-vs-run 檢視
  settingsOpen.value = false; // 確認即收面板：測試結果/執行日誌立即可見
  activeTab.value = 'results';
  try {
    const body: PromptSandboxStartBody =
      props.scope === 'all'
        ? targets.scopeBody(promptArgs.value)
        : {
            source: props.source,
            item_ids: props.sourceIds,
            prompt_ids: promptArgs.value,
            scope: props.scope,
          };
    body.llm_config_id = llmConfigId.value || undefined;
    if (Object.keys(versionSelection.value.versions).length) {
      body.versions = versionSelection.value.versions;
    }
    // 草稿模式：送測時取 DB 草稿內容快照帶入（後端逐條強驗 + 落庫快照，與草稿後續演進脫鉤）
    if (draftCodes.value.length) {
      const fetched = await Promise.all(
        draftCodes.value.map(async (code) => ({ code, ...(await getRuleDraft(code)) })),
      );
      const drafts: Record<string, string> = {};
      for (const { code, draft } of fetched) {
        const text = typeof draft?.content.text === 'string' ? draft.content.text : '';
        if (!text.trim()) {
          throw new Error(`「${rulesStore.labelFor(code)}」草稿不存在或為空，請先編輯儲存`);
        }
        drafts[code] = text;
      }
      body.drafts = drafts;
      body.compare = compareEnabled.value;
    }
    const { job_id } = await startPromptSandbox(body);
    if (token !== runSeq) return;
    _openLogStream(job_id); // 執行日誌分頁即時串流（與輪詢並行，互不影響）
    // 輪詢至終態（done/error）；沙盒非長批次，短間隔即可即時反映進度。
    while (true) {
      await new Promise((r) => setTimeout(r, 700));
      if (token !== runSeq) return;
      const snap = await getPromptSandboxStatus(job_id);
      if (token !== runSeq) return;
      if (snap.status === 'done' && snap.run_id) {
        const detail = _normalizeRun(await getPromptSandboxRun(snap.run_id));
        if (token !== runSeq) return;
        activeRun.value = detail;
        _closeLogStream();
        logEntries.value = detail.log; // 改顯示落庫的權威快照（避免 SSE 重連/漏幀差異）
        await loadHistory();
        break;
      }
      if (snap.status === 'error') {
        _closeLogStream();
        Message.error('測試任務失敗');
        break;
      }
    }
  } catch (e) {
    if (token === runSeq) Message.error(e instanceof Error ? e.message : '測試失敗');
  } finally {
    if (token === runSeq) running.value = false;
  }
}

// ── 測試歷史（與正式初判歷史完全分離）──
const history = ref<PromptSandboxRunSummary[]>([]);
const historyLoading = ref(false);
async function loadHistory() {
  historyLoading.value = true;
  try {
    const r = await listPromptSandboxRuns();
    history.value = r.items;
  } catch (e) {
    Message.error(e instanceof Error ? e.message : '載入測試歷史失敗');
  } finally {
    historyLoading.value = false;
  }
}
/** 查看某次歷史測試：拉完整詳情（含 results + log 快照）並切到結果分頁；log 分頁同步顯示
 * 當時的完整快照（靜態，非串流）——需求「透過測試歷史回看當時的完整 log」的落地點。 */
async function viewHistoryRun(runId: string) {
  try {
    _closeLogStream(); // 回看歷史時若有正在跑的即時串流先關閉，避免與靜態快照混淆
    runCompare.value = null; // 離開 run-vs-run 檢視
    activeRun.value = _normalizeRun(await getPromptSandboxRun(runId));
    logEntries.value = activeRun.value.log;
    activeTab.value = 'results';
  } catch (e) {
    Message.error(e instanceof Error ? e.message : '載入測試紀錄失敗');
  }
}

/** 測試歷史勾恰兩筆 → run-vs-run 對比（後端按 source_id 對齊 + metrics），結果分頁顯示。 */
const comparing = ref(false);
async function doCompareRuns() {
  if (compareSelection.value.length !== 2) return;
  comparing.value = true;
  try {
    const [a, b] = compareSelection.value;
    runCompare.value = await comparePromptSandboxRuns(a, b);
    activeTab.value = 'results';
  } catch (e) {
    Message.error(e instanceof Error ? e.message : '對比失敗');
  } finally {
    comparing.value = false;
  }
}

/** 範圍中文標籤（歷史列表用）。selection 為舊版「已選 N」按鈕遺留的歷史紀錄值（該按鈕已併入
 * all 的「已選內」子模式，觸發端不再產生新的 selection，此處僅為相容顯示舊資料）。 */
const SCOPE_LABEL: Record<string, string> = {
  single: '單列',
  selection: '選取',
  all: '批量',
};

// 開啟時重置狀態 + 載入歷史（選哪些 prompt 由 PromptVersionPickerGroup 的開關預設，見
// usePromptVersionPicker：預設僅 polarity 開，免每次手動勾）；scope='all' 時初始化目標選取器。
watch(
  () => props.visible,
  async (v) => {
    if (!v) {
      runSeq += 1; // 作廢進行中的輪詢迴圈：關抽屜後不再打 API、不覆寫重開後的畫面
      running.value = false; // 被作廢的迴圈不會再動 running（token 已過期），這裡顯式復位
      activeRun.value = null;
      runCompare.value = null;
      compareSelection.value = [];
      _closeLogStream();
      logEntries.value = [];
      return;
    }
    activeTab.value = 'results';
    sandboxPanel.value = props.scope === 'all' ? 'target' : 'settings';
    settingsOpen.value = false;
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
        panel-class="w-[560px] max-h-[70vh]"
      >
        <a-menu
          v-if="scope === 'all'"
          :selected-keys="[sandboxPanel]"
          class="mb-2 rounded border"
          @menu-item-click="(k: string) => (sandboxPanel = k as 'target' | 'settings')"
        >
          <a-menu-item key="target">目標範圍</a-menu-item>
          <a-menu-item key="settings">初判設定</a-menu-item>
        </a-menu>

        <!-- 目標範圍（scope='all'，比照初判分類目標選取；adhoc＝臨時貼 ID）。
             兩個子面板用 v-show（非 v-if）常駐掛載：初判設定面板內的 PromptVersionPickerGroup
             需要 onMounted 才會 emit 預設勾選——批量預設停在「目標範圍」，若用 v-if 該元件
             不掛載，selectedCodes 恆空、「確認」永遠 disabled。 -->
        <div v-show="sandboxPanel === 'target' && scope === 'all'">
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

        <!-- 初判設定：模型 + 開關控制是否納入本次測試（每支預設沿用 active，可個別切換歷史版本）；
             scope='single' 恆顯示這裡（sandboxPanel 恆為 settings）。 -->
        <div v-show="sandboxPanel === 'settings' || scope !== 'all'">
          <LlmConfigSelect v-model="llmConfigId" :configs="llmConfigs" class="mb-2" />
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
                      <a-alert v-if="item.error" type="error" :content="item.error" />
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

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
 * 所有 Prompt 測試都在此抽屜進行，不支援測試未存檔草稿——版本選擇（PromptVersionPickerGroup）
 * 只能選已存檔的歷史版本；規則配置頁不再有「測試這份草稿」入口。
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { Message } from '@arco-design/web-vue';
import {
  getPromptSandboxRun,
  getPromptSandboxStatus,
  listPromptSandboxRuns,
  prejudgeLogStreamUrl,
  startPromptSandbox,
  type PromptSandboxItemResult,
  type PromptSandboxRunSummary,
  type PromptSandboxStartBody,
} from '@/api';
import { fmtDt } from '../utils';
import type { ProblemRow } from '../constants/source-schema.constant';
import type { CascadeNode } from '@/api';
import { CollapsibleSidePanel, StickyTabs, TableLayout } from '@/components';
// 相對路徑 import（非走 barrel）：本檔自身即為 components barrel 的一員，經 barrel 迴繞 import
// 同資料夾元件會觸發 circular dep（見 barrel-exports 規則）。
import AttributionFilterBar from './AttributionFilterBar.vue';
import LlmConfigSelect from './LlmConfigSelect.vue';
import PrejudgeLogView from './PrejudgeLogView.vue';
import PromptVersionPickerGroup from './PromptVersionPickerGroup.vue';
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

// 7 支 prompt 的 rule_code/選版本/開關已下沉進 PromptVersionPickerGroup（含 store.loadList 載入），
// 本檔不再需要自己拉 judgeRulesStore。
const selectedCodes = ref<string[]>([]);
const { llmConfigId, llmConfigs } = useLlmConfigs();
const versionSelection = ref<{ versions: Record<string, number> }>({ versions: {} });
/** rule_code（prompt_C-3）→ 端點值（C-3 / polarity）。 */
const toPromptArg = (code: string): string => code.replace('prompt_', '');
const promptArgs = computed(() => selectedCodes.value.map(toPromptArg));

// ── scope='all' 依條件批量選取（比照初判分類 usePrejudgeJob 的目標選取 pattern）──
const targets = usePromptSandboxTargets({
  source: () => props.source,
  effVerticals: () => props.effVerticals,
  selectedKeys: () => props.selectedKeys ?? [],
  listFilters: () => props.listFilters ?? {},
});
/** 依條件批量選取的目標篩選欄位子集（順序對齊初判分類 PREJUDGE_TARGET_FIELDS；
 * 判決階段由上方 checkbox 承擔，不納入此欄）。 */
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
/** 判決設定面板是否展開；預設收合，讓執行日誌/測試結果直接可見，不被設定區擠到下面。 */
const settingsOpen = ref(false);
const running = ref(false);
type RunDetail = PromptSandboxRunSummary & {
  results: PromptSandboxItemResult[];
  log: LogEntry[];
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
    activeRun.value = _normalizeRun(await getPromptSandboxRun(runId));
    logEntries.value = activeRun.value.log;
    activeTab.value = 'results';
  } catch (e) {
    Message.error(e instanceof Error ? e.message : '載入測試紀錄失敗');
  }
}

/** 範圍中文標籤（歷史列表用）。selection 為舊版「已選 N」按鈕遺留的歷史紀錄值（該按鈕已併入
 * all 的「已選內」子模式，觸發端不再產生新的 selection，此處僅為相容顯示舊資料）。 */
const SCOPE_LABEL: Record<string, string> = {
  single: '單列',
  selection: '選取',
  all: '批量',
};

/** 域條目判準：有 domain_label 欄位＝域 prompt 結果；否則為 polarity 條目。 */
const isDomainEntry = (p: NonNullable<PromptSandboxItemResult['prompts']>[number]): boolean =>
  p.domain_label !== undefined;

// 開啟時重置狀態 + 載入歷史（選哪些 prompt 由 PromptVersionPickerGroup 的開關預設，見
// usePromptVersionPicker：預設僅 polarity 開，免每次手動勾）；scope='all' 時初始化目標選取器。
watch(
  () => props.visible,
  async (v) => {
    if (!v) {
      runSeq += 1; // 作廢進行中的輪詢迴圈：關抽屜後不再打 API、不覆寫重開後的畫面
      running.value = false; // 被作廢的迴圈不會再動 running（token 已過期），這裡顯式復位
      activeRun.value = null;
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
    title="Prompt 測試（沙盒 · 不受正式歸因閘門限制 · 不落正式判決）"
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

    <!-- 左側收合軌 + 右側主內容：判決設定（模型/版本，scope='all' 再加目標範圍）預設收合，
         點窄直排 tab 展開/收合，收合時執行日誌／測試結果直接可見，不被設定區擠到下面。
         面板用 v-show（非 v-if）保持掛載：即使收合，PromptVersionPickerGroup 的預設勾選仍會
         立即生效，避免「確認」按鈕因元件未掛載而誤判為未選任何 prompt。 -->
    <div class="flex min-h-0 flex-1 gap-3 overflow-hidden">
      <CollapsibleSidePanel
        v-model="settingsOpen"
        label="判決設定"
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
          <a-menu-item key="settings">判決設定</a-menu-item>
        </a-menu>

        <!-- 目標範圍（scope='all'，比照初判分類目標選取；adhoc＝臨時貼 ID）。
             兩個子面板用 v-show（非 v-if）常駐掛載：判決設定面板內的 PromptVersionPickerGroup
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
              <div class="mb-1 text-xs text-[var(--color-text-3)]">目標判決階段（預設只測未判）</div>
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
              日期 / ID / 外部評論 對所有目標生效；傾向 / 信心分層 / 歸因分類 僅對「已判」階段生效。
            </div>
          </template>
          <div class="mt-2 text-xs text-[var(--color-text-3)]">
            將測試 {{ targets.targetCount.value }} 筆
          </div>
        </div>

        <!-- 判決設定：模型 + 開關控制是否納入本次測試（每支預設沿用 active，可個別切換歷史版本）；
             scope='single' 恆顯示這裡（sandboxPanel 恆為 settings）。 -->
        <div v-show="sandboxPanel === 'settings' || scope !== 'all'">
          <LlmConfigSelect v-model="llmConfigId" :configs="llmConfigs" class="mb-2" />
          <div class="mb-1 text-xs text-[var(--color-text-3)]">
            Prompt 版本（開關控制是否納入本次測試；每支預設沿用 active，可個別切換歷史版本）
          </div>
          <PromptVersionPickerGroup
            :with-toggle="true"
            @update:resolved="(v) => (versionSelection = v)"
            @update:enabled-codes="(codes) => (selectedCodes = codes)"
          />
          <div class="mt-3 text-xs text-[var(--color-text-3)]">
            確認後開始測試，過程會消耗 token（不落正式判決、不受正式閘門限制）。
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
              <template v-else-if="activeRun">
                <div class="mb-2 text-xs text-[var(--color-text-3)]">
                  {{ fmtDt(activeRun.created_at) }} · model={{ activeRun.model }} ·
                  {{ activeRun.item_count }} 筆
                </div>
                <div class="flex flex-col gap-3">
                  <div
                    v-for="item in activeRun.results"
                    :key="item.source_id"
                    class="rounded-lg border p-3"
                  >
                    <div class="mb-2 flex items-center gap-2">
                      <span class="font-mono text-xs text-[var(--color-text-3)]">{{
                        item.source_id
                      }}</span>
                      <a-tag v-if="item.polarity" size="small">{{ item.polarity }}</a-tag>
                    </div>
                    <a-alert v-if="item.error" type="error" :content="item.error" />
                    <div v-else class="flex flex-col gap-2">
                      <div
                        v-for="(p, i) in item.prompts"
                        :key="i"
                        class="rounded border-l-2 border-[var(--color-border-3)] bg-[var(--color-fill-1)] px-2 py-1.5 text-xs"
                      >
                        <template v-if="isDomainEntry(p)">
                          <div class="flex items-center gap-1.5">
                            <a-tag size="small" :color="p.matched ? 'green' : 'gray'">{{
                              p.matched ? '✅ 命中' : '⭕ 棄權'
                            }}</a-tag>
                            <span class="font-medium">{{ p.domain_label }}</span>
                            <template v-if="p.matched && p.attributions?.[0]">
                              <span class="text-[var(--color-text-3)]">›</span>
                              <span>{{ p.attributions[0].l2_label }}</span>
                              <span class="ml-auto font-mono text-[11px] text-[var(--color-text-3)]"
                                >{{ Math.round((p.attributions[0].confidence ?? 0) * 100) }}%</span
                              >
                            </template>
                          </div>
                          <div class="mt-1 text-[var(--color-text-2)]">
                            {{ p.matched ? p.attributions?.[0]?.reason : p.abstain_reason }}
                          </div>
                        </template>
                        <template v-else>
                          <div class="flex items-center gap-1.5">
                            <a-tag size="small" color="arcoblue">極性</a-tag>
                            <span class="font-medium">{{ p.polarity }}</span>
                            <span class="ml-auto font-mono text-[11px] text-[var(--color-text-3)]"
                              >情緒 {{ p.sentiment_score }}</span
                            >
                          </div>
                          <div v-if="p.reason" class="mt-1 text-[var(--color-text-2)]">
                            {{ p.reason }}
                          </div>
                        </template>
                      </div>
                    </div>
                  </div>
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
              >
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
                    <template #cell="{ record }">{{ record.prompt_ids.join('、') }}</template>
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
  </a-drawer>
</template>

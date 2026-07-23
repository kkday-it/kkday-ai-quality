// 「確認初判分類」抽屜的批量／單列共用確認流程——由 AttributionList.vue 下沉。
// 抽屜本身的 template（CollapsibleSidePanel/LlmConfigPicker/LlmKnobs/PromptVersionPickerGroup 等）
// 留在頁面，本 composable 只承接狀態計算與決策邏輯（開哪種 scope / 送出時呼叫誰 / 確認文案組字）。
import { computed, ref, type Ref } from 'vue';
import { composeLlmLabel } from '@/features/settings/utils';
import { useJudgeRulesStore } from '@/stores/judgeRules.store';
import type { LogEntry } from '../components/PrejudgeLogView.types';

/** 跟隨 useLlmAreaDefault('prejudge') 的旋鈕形狀（僅取確認文案組字所需欄位）。 */
interface LlmKnobsLike {
  model: string;
  thinking: string;
  reasoning_effort: string;
}

/** 上一輪 job 終態快照（見 usePrejudgeJob.lastRun）；此處只需能被重置為 null，不依賴其餘欄位形狀。 */
interface PrejudgeLastRunLike {
  model: string;
}

/** useRejudgeConfirm 的注入依賴（皆來自 useAttributionList 展開的 usePrejudgeJob / useLlmAreaDefault）。 */
interface RejudgeConfirmDeps {
  /** 確認抽屜開關（批量／單列共用同一顆，來自 usePrejudgeJob.confirmOpen）。 */
  confirmOpen: Ref<boolean>;
  /** 執行日誌（開新一輪前清空，避免誤讀上一輪殘留）。 */
  logEntries: Ref<LogEntry[]>;
  logError: Ref<string>;
  lastRun: Ref<PrejudgeLastRunLike | null>;
  /** 目前 prejudge 功能區的 LLM provider（跟隨 useLlmAreaDefault('prejudge')）。 */
  llmProvider: Ref<string>;
  /** 目前 prejudge 功能區的旋鈕（reactive，非 ref）。 */
  llmKnobs: LlmKnobsLike;
  /**
   * 單列（重新）初判執行者：由呼叫端注入（可能包含執行後 UI 副作用如捲動定位，
   * composable 本身不處理 DOM，僅負責決定「該不該呼叫它」）。
   */
  runRejudgeRow: (id: string, promptVersions?: Record<string, number>) => void | Promise<void>;
  /** 批量初判執行者（usePrejudgeJob.doRun）。 */
  runBatch: (promptVersions?: Record<string, number>) => void | Promise<void>;
  /** 開批量初判前的目標範圍準備（usePrejudgeJob.openPrejudge：算 targetCount 等並開抽屜）。 */
  openBatchTargeting: () => void;
}

/**
 * 「確認初判分類」抽屜的批量／單列共用狀態與決策邏輯。
 *
 * @param deps 注入依賴（見 {@link RejudgeConfirmDeps}）。
 * @returns 抽屜範圍狀態（confirmScope/confirmRowId/confirmSettingsOpen/confirmVersionSelection）、
 *   開抽屜動作（openRowConfirm/openBatchConfirm）、送出決策（onConfirmRun）、
 *   確認文案（currentLlmLabel/rejudgeConfirmText/confirmModelLabel/confirmPinnedVersions）。
 */
export function useRejudgeConfirm(deps: RejudgeConfirmDeps) {
  const {
    confirmOpen,
    logEntries,
    logError,
    lastRun,
    llmProvider,
    llmKnobs,
    runRejudgeRow,
    runBatch,
    openBatchTargeting,
  } = deps;

  // ── 確認初判分類抽屜：批量（工具列）與單列（操作欄）共用同一個 confirmOpen 抽屜，
  //    confirmScope 分流內容顯示；confirmRowId 僅 scope='row' 時有值 ──
  const confirmScope = ref<'batch' | 'row'>('batch');
  const confirmRowId = ref('');
  /** 初判設定/目標範圍面板是否展開。開抽屜時預設**展開**——「確認」按鈕收在面板 footer 內
   * （面板＝確認表單），預設收合會把主行為藏起來多一次點擊；確認後自動收合改看執行日誌。 */
  const confirmSettingsOpen = ref(false);
  const confirmVersionSelection = ref<{ versions: Record<string, number> }>({ versions: {} });

  /** 開單列「確認初判分類」抽屜（清掉上一輪執行殘留的日誌/終態摘要）。 */
  const openRowConfirm = (record: { _group: unknown }): void => {
    confirmScope.value = 'row';
    confirmRowId.value = String(record._group);
    confirmSettingsOpen.value = true;
    logEntries.value = []; // 清掉上一次執行殘留的日誌，避免誤讀成本次結果
    logError.value = '';
    lastRun.value = null; // 新一輪確認流程開始，清上一輪終態摘要
    confirmOpen.value = true;
  };
  /** 開批量「確認初判分類」抽屜（委派 openBatchTargeting 算目標範圍並開抽屜）。 */
  const openBatchConfirm = (): void => {
    confirmScope.value = 'batch';
    confirmSettingsOpen.value = true;
    logEntries.value = [];
    logError.value = '';
    openBatchTargeting();
  };
  /** 抽屜「確認」：依 confirmScope 分流批量／單列執行（草稿不進正式初判，僅傳版本選擇）。
   * 不關閉抽屜——確認後自動收合設定面板，下方常駐的執行日誌區就地串流；關閉抽屜走右上 X
   * （不影響背景 job）。 */
  const onConfirmRun = (): void => {
    confirmSettingsOpen.value = false;
    if (confirmScope.value === 'row') {
      runRejudgeRow(confirmRowId.value, confirmVersionSelection.value.versions);
    } else {
      runBatch(confirmVersionSelection.value.versions);
    }
  };

  // ── 確認抽屜執行前摘要卡：把「這次會用什麼設定跑」攤開給使用者看（模型 + 版本選擇），
  //    取代原本收合面板時的大片空白；label 復用 judgeRules store 與 composeLlmLabel，勿另建對照。 ──
  const judgeRulesStore = useJudgeRulesStore();
  const confirmModelLabel = computed(() =>
    llmKnobs.model
      ? composeLlmLabel({
          provider: llmProvider.value,
          model: llmKnobs.model,
          thinking: llmKnobs.thinking,
          reasoning_effort: llmKnobs.reasoning_effort,
        })
      : '系統預設模型',
  );
  /** 指定了非 active 歷史版本的 prompt 清單（[中文名, 版本號]）；空＝全部沿用 active。 */
  const confirmPinnedVersions = computed(() =>
    Object.entries(confirmVersionSelection.value.versions).map(
      ([code, ver]) => [judgeRulesStore.labelFor(code), ver] as const,
    ),
  );

  /** 本次初判將使用的模型 label（跟隨 prejudge 功能區默認，抽屜可臨時覆寫）；無配置回空。 */
  const currentLlmLabel = computed(() =>
    llmKnobs.model
      ? composeLlmLabel({
          provider: llmProvider.value,
          model: llmKnobs.model,
          thinking: llmKnobs.thinking,
          reasoning_effort: llmKnobs.reasoning_effort,
        })
      : '',
  );

  /** 單列（重）判抽屜的說明文案（附當前模型，判前提醒用什麼 model 歸因）；有既有歸因時提醒會覆寫。 */
  const rejudgeConfirmText = computed(
    () =>
      `將以「${currentLlmLabel.value || '（無 LLM 配置）'}」對此列進行初判分類（若已有歸因將覆寫，人工真值標註保留），並消耗初判額度。`,
  );

  return {
    confirmScope,
    confirmRowId,
    confirmSettingsOpen,
    confirmVersionSelection,
    openRowConfirm,
    openBatchConfirm,
    onConfirmRun,
    confirmModelLabel,
    confirmPinnedVersions,
    currentLlmLabel,
    rejudgeConfirmText,
  };
}

<script setup lang="ts">
/**
 * Prompt 調試台「跑批」抽屜：上傳 CSV/XLSX，以**當前編輯中的 Prompt／輸出契約**
 * 整批結構化裁決（等同離線 lab 跑批的 app 原生版：app 連線 key、strict schema、斷點續跑）。
 *
 * - 每次啟動建獨立 run 目錄（`data/prompt_debug_batch/<run_id>/`，host 可直取產物）；
 *   Prompt/契約/模型以啟動當下快照鎖進 manifest，之後編輯不影響已建 run，續跑重放同一套。
 * - raw_results.jsonl 逐筆落盤＝斷點：中斷/失敗後「續跑」只補未成功筆，「重跑」忽略斷點全部重打。
 * - 進度走輪詢（in-mem 快照）；server 重啟後 run 變 interrupted，仍可從列表續跑。
 *
 * **多模型並行**（2026-07-30）：model 選擇改 multi-select，統一走群組啟動端點
 * （`startPromptDebugBatchGroup`）——單選一個 model 時等同「群組大小為 1」，前端只維護
 * 一套啟動路徑，不因選了幾個 model 分岔。每個 model 各自解出自己的供應商（顯示在選項旁，
 * 讓「這個 model 會打到哪」在送出前就可見），各自獨立起一個完整單模型 run，互不影響。
 */
import { computed, reactive, ref, watch } from 'vue';
import { Message, type FileItem, type TableColumnData, type TableData } from '@arco-design/web-vue';
import type { TableOperationColumn } from '@arco-design/web-vue/es/table/interface';
import { IconDelete, IconDownload, IconEye, IconFile, IconPlayArrow, IconRecordStop, IconRefresh, IconSync } from '@arco-design/web-vue/es/icon';
import { useIntervalFn } from '@vueuse/core';
import {
  cancelPromptDebugBatchRun,
  downloadPromptDebugBatchFile,
  getPromptDebugBatchGroup,
  getPromptDebugBatchRun,
  listPromptDebugBatchRuns,
  resumePromptDebugBatchRun,
  startPromptDebugBatchGroup,
  type PromptDebugBatchFileKind,
  type PromptDebugBatchGroupMember,
  type PromptDebugBatchRunRow,
  type PromptDebugBatchSnapshot,
  type PromptDebugBatchStatus,
  type PromptDraftMeta,
  type PromptReleaseMeta,
} from '@/api';
import { LlmConfigSelect } from '@/components';
import { fmtPercent } from '@/utils';
import type { LlmModelConfig } from '@/features/settings/types';
import { SOURCES } from '../constants';
import { fmtBeijingDt, fmtDurationSec } from '../utils';
// 相對路徑（非走 barrel）：本檔自身即為 components barrel 的一員，經 barrel 迴繞會觸發 circular dep。
import PromptVersionSelect from './PromptVersionSelect.vue';

const props = defineProps<{
  /** 是否顯示。 */
  visible: boolean;
  /** 頁面編輯框當前內容（「用頁面當前內容」這個選項的來源）。 */
  systemPrompt: string;
  /** 頁面當前選定的版本名（摘要用）。 */
  promptVersion: string;
  /** 頁面當前口徑軌（`draft` / `release`）；編輯框已偏離時為空字串。 */
  promptKind: string;
  /** 編輯框已偏離所選版本＝那份內容不對應任何存檔版（摘要要講清楚）。 */
  promptEdited: boolean;
  /** 草稿／正式版清單（與頁面「版本列表」同一份資料源）——跑批不必被頁面編輯器綁死，
   * 可直接挑任一已存檔版本開跑，免去「關抽屜→切軌→選版→重開」這段繞路。 */
  drafts: PromptDraftMeta[];
  releases: PromptReleaseMeta[];
  /** 可選的模型配置清單（出廠種子 ++ 使用者自訂，見 `useLlmAreaConfig`）。 */
  configs: LlmModelConfig[];
  /** 頁面當前選中的配置 id；抽屜開啟時預選它（維持「跑批跟著頁面設定走」的既有習慣）。 */
  defaultConfigId: string;
  /** 各供應商是否已配 token（下拉狀態點用）。 */
  providerHasToken: Record<string, boolean>;
}>();

const emit = defineEmits<{ (e: 'update:visible', v: boolean): void }>();

/** 輪詢間隔／連續失敗上限：非跨環境值，具名避免裸數字。 */
const POLL_MS = 1500;
const POLL_MAX_ERRORS = 5;
/** 與後端 `prompt_debug_batch._MAX_ENTRIES_PER_GROUP` 同步（純顯示用；實際上限仍由後端把關，
 * 這裡超額只是提前給使用者一個明確提示，不是安全邊界）。 */
const MAX_CONFIGS_PER_GROUP = 6;

const form = reactive({
  file: null as File | null,
  sheet: '',
  idColumn: 'session_oid',
  textColumn: 'conversation_full',
  limit: 10,
});

// ── 本批要跑哪一份 Prompt ────────────────────────────────────────────────────────
/**
 * Prompt 來源：`current`＝頁面編輯框當前內容（維持既有預設行為）｜`version`＝直接挑一個已存檔版本。
 *
 * 後端收的一律是**全文**、再由 `prompt_debug_versions.resolve()` 反查它等於哪一版，所以這裡選版
 * 只需把該版全文取回來送出即可，端點不必新增任何版本參數。
 */
const promptSource = ref<'current' | 'version'>('current');
const versionKey = ref('');
const versionText = ref('');
const loadingVersion = ref(false);

// ── 資料來源：上傳檔案 vs 貼 ID 從 DB 撈 ─────────────────────────────────────────
/**
 * 輸入方式。`upload`＝既有的 CSV/XLSX；`db`＝貼一串自然鍵（如 session_oid）+ 選反饋來源，
 * 由後端從來源表撈對話內容。
 *
 * DB 模式下 ID 欄名／對話欄名由來源註冊表決定（`conversations` → `session_oid`），
 * 不再由使用者填——那兩個欄名本來就是「描述上傳檔長什麼樣」，DB 取數時沒有意義。
 */
const inputMode = ref<'upload' | 'db'>('upload');
const dbSource = ref(SOURCES[0]?.value ?? '');
const dbIdsText = ref('');

const SOURCE_OPTS = SOURCES.map((s) => ({ value: s.value, label: s.label }));

/** 貼上的 ID（換行／逗號／空白皆可分隔，保序去重）——與後端的切分規則一致。 */
const dbIds = computed(() => [
  ...new Set(
    dbIdsText.value
      .split(/[\s,]+/)
      .map((s) => s.trim())
      .filter(Boolean),
  ),
]);

/** 本批實際要送出的 Prompt 全文。 */
const effectivePrompt = computed(() =>
  promptSource.value === 'version' ? versionText.value : props.systemPrompt,
);

/**
 * 摘要列要顯示的版本身分。
 *
 * 舊文案寫死「最新版 {name}」，但頁面下拉本來就能挑歷史草稿或歷史正式版，切過去之後那句話就是錯的
 * ——這裡一律講「實際是哪一版」，不再假設它是最新的。
 */
const promptSummary = computed(() => {
  if (promptSource.value === 'version') {
    if (!versionKey.value) return '尚未選擇版本';
    const sep = versionKey.value.indexOf(':');
    const kind = versionKey.value.slice(0, sep) === 'release' ? '正式版' : '草稿';
    return `${kind} ${versionKey.value.slice(sep + 1)}`;
  }
  if (props.promptEdited) return '頁面臨時編輯版（未存檔）';
  const kind = props.promptKind === 'release' ? '正式版' : '草稿';
  return props.promptVersion ? `${kind} ${props.promptVersion}` : '—';
});

/** 欲並行的模型配置 id 清單；開抽屜時預選頁面當前那筆（見下方 watch）。 */
const selectedConfigIds = ref<string[]>([]);

watch(
  () => props.visible,
  (visible) => {
    if (visible && selectedConfigIds.value.length === 0 && props.defaultConfigId) {
      selectedConfigIds.value = [props.defaultConfigId];
    }
  },
  { immediate: true },
);

/** 選中的配置（保持使用者的勾選順序，結果分欄照此序顯示）。 */
const selectedConfigs = computed(() =>
  selectedConfigIds.value
    .map((id) => props.configs.find((c) => c.id === id))
    .filter((c): c is LlmModelConfig => !!c),
);

const starting = ref(false);
const runs = ref<PromptDebugBatchRunRow[]>([]);
const loadingRuns = ref(false);
/** 跑批記錄載入失敗訊息（持久顯示；空＝正常）。 */
const runsError = ref('');
const activeRunId = ref('');
const activeSnap = ref<PromptDebugBatchSnapshot | null>(null);
const etaText = ref('');
let pollErrors = 0;

// ── 多模型群組：啟動結果 + 群組進度輪詢（獨立於下方單 run 詳情追蹤）────────────────
const activeGroupId = ref('');
const groupMembers = ref<PromptDebugBatchGroupMember[]>([]);
const groupRuns = ref<PromptDebugBatchRunRow[]>([]);

const { pause: pauseGroupPoll, resume: resumeGroupPoll } = useIntervalFn(pollGroup, POLL_MS, {
  immediate: false,
});

async function pollGroup(): Promise<void> {
  if (!activeGroupId.value) {
    pauseGroupPoll();
    return;
  }
  try {
    const { runs: rows } = await getPromptDebugBatchGroup(activeGroupId.value);
    groupRuns.value = rows;
    if (rows.every((r) => r.status !== 'running' && r.status !== 'cancelling')) {
      pauseGroupPoll();
      await refreshRuns();
    }
  } catch {
    /* 群組輪詢失敗不彈錯——單 run 的輪詢已有獨立錯誤提示，避免同時彈兩份 */
  }
}

/** 合併啟動結果（member：ok/error/provider）與輪詢進度（run：狀態/計數），
 * 供群組總覽區一次渲染，避免模板內重複 `.find()`。 */
const groupOverview = computed(() =>
  groupMembers.value.map((member) => ({
    member,
    run: member.run_id ? (groupRuns.value.find((r) => r.run_id === member.run_id) ?? null) : null,
  })),
);

const isXlsx = computed(() => /\.(xlsx|xlsm)$/i.test(form.file?.name ?? ''));
/** 輸入端是否備妥（上傳＝有檔；DB＝選了來源且至少貼了一個 id）。 */
const inputReady = computed(() =>
  inputMode.value === 'upload' ? !!form.file : !!dbSource.value && dbIds.value.length > 0,
);

const canStart = computed(
  () =>
    inputReady.value &&
    !!effectivePrompt.value.trim() &&
    !loadingVersion.value &&
    selectedConfigs.value.length > 0 &&
    selectedConfigs.value.length <= MAX_CONFIGS_PER_GROUP &&
    !starting.value,
);

/**
 * 進度百分比（0–1，Arco a-progress 口徑）：斷點復用 + 本次完成 / 目標。
 *
 * ⚠️ `total` 可能是 `null`（server 重啟後由磁碟推導的 run 拿不到當時的選中總數）。
 * 那是「未知」不是「0」——分母未知時不該畫出一條 0% 的進度條假裝知道進度，回 null 讓呼叫端
 * 改顯示不確定態。過去後端在這個情境回 0，畫面上就出現「目標 0」與「成功 42」並列的矛盾。
 */
const pct = computed<number | null>(() => {
  const s = activeSnap.value;
  if (!s || !s.total) return s?.total === null ? null : 0;
  return Math.min(1, (s.resumed + s.processed) / s.total);
});

const STATUS_META: Record<PromptDebugBatchStatus, { label: string; color: string }> = {
  running: { label: '執行中', color: 'arcoblue' },
  cancelling: { label: '停止中', color: 'orangered' },
  done: { label: '完成', color: 'green' },
  error: { label: '失敗', color: 'red' },
  cancelled: { label: '已停止', color: 'gray' },
  interrupted: { label: '中斷（可續跑）', color: 'orange' },
};

/** 狀態顯示 meta（未知狀態原字灰標，避免模板內型別斷言）。 */
const statusMeta = (status: string): { label: string; color: string } =>
  STATUS_META[status as PromptDebugBatchStatus] ?? { label: status, color: 'gray' };

const { pause: pausePoll, resume: resumePoll } = useIntervalFn(pollActive, POLL_MS, {
  immediate: false,
});

/** 追蹤某 run 的即時進度（啟動/續跑/點列表執行中列時呼叫）。 */
function track(runId: string): void {
  activeRunId.value = runId;
  pollErrors = 0;
  void pollActive();
  resumePoll();
}

async function pollActive(): Promise<void> {
  if (!activeRunId.value) {
    pausePoll();
    return;
  }
  try {
    const snap = await getPromptDebugBatchRun(activeRunId.value);
    pollErrors = 0;
    activeSnap.value = snap;
    updateEta(snap);
    if (snap.status !== 'running' && snap.status !== 'cancelling') {
      pausePoll();
      await refreshRuns();
    }
  } catch (error) {
    // 連續多次輪詢失敗（如 server 重啟）才停，避免瞬時抖動誤停
    if (++pollErrors >= POLL_MAX_ERRORS) {
      pausePoll();
      Message.warning(error instanceof Error ? error.message : '進度輪詢中斷');
    }
  }
}

/** 由快照算本次速度與 ETA（只計本次新完成，不含斷點復用）。 */
function updateEta(snap: PromptDebugBatchSnapshot): void {
  // total 未知（磁碟推導的 run）就算不出「還剩幾筆」，寧可不顯示也不要編一個數字出來
  if (!snap.elapsed_sec || !snap.processed || snap.status !== 'running' || snap.total === null) {
    etaText.value = '';
    return;
  }
  // 已跑秒數由後端算（它才知道本段起點）；前端不再自己拿 started_at 減現在——那個欄位已改 ISO，
  // 而且「後端時鐘」與「瀏覽器時鐘」有偏差時算出來的速度會失真
  const elapsed = Math.max(1, snap.elapsed_sec);
  const rate = snap.processed / elapsed;
  const remaining = Math.max(0, snap.total - snap.resumed - snap.processed);
  const eta = rate > 0 ? remaining / rate : 0;
  const mm = Math.floor(eta / 60);
  const ss = Math.round(eta % 60);
  etaText.value = `${rate.toFixed(2)} 條/秒 · 預估剩 ${mm ? `${mm} 分 ` : ''}${ss} 秒`;
}

async function refreshRuns(): Promise<void> {
  loadingRuns.value = true;
  runsError.value = '';
  try {
    runs.value = (await listPromptDebugBatchRuns()).runs;
  } catch (error) {
    // ⚠️ 一次性 toast 不足以充當 error 態：錯過那 3 秒之後，畫面就只剩一張空表格，
    //   看起來像「還沒跑過任何 run」而不是「清單載入失敗」。持久錯誤條才是三態的 error。
    runsError.value = error instanceof Error ? error.message : '載入跑批記錄失敗';
    Message.error(runsError.value);
  } finally {
    loadingRuns.value = false;
  }
}

/**
 * 上傳清單（受控）。
 *
 * 走受控是為了能自己畫 list item：Arco 預設的 item 在 `:auto-upload="false"` 下會恆常渲染一顆
 * 「開始上傳」play 圖示（檔案永遠停在 `status: 'init'`），而本元件根本沒有 `action`，點下去只會
 * 對當前頁面 POST 一發必然失敗的請求、把檔案標成紅色錯誤態。那顆按鈕與下方「開始跑批」毫無關係，
 * 純粹是誤導，所以改用 `#upload-item` 自繪：只留檔名與刪除。
 */
const fileList = ref<FileItem[]>([]);

/** Arco upload 以 fileList 形態 emit；:auto-upload=false 下僅取原生 File 自行送 multipart。 */
function onFileChange(list: FileItem[]): void {
  // :limit=1 但 Arco 仍會把新檔 append 進來，取最後一筆＝使用者最新選的那個
  fileList.value = list.slice(-1);
  form.file = fileList.value[0]?.file ?? null;
}

/** 移除已選檔（自繪 item 的刪除鈕）。 */
function clearFile(): void {
  fileList.value = [];
  form.file = null;
}

async function onStart(): Promise<void> {
  if (!canStart.value) return;
  const usingDb = inputMode.value === 'db';
  starting.value = true;
  try {
    const result = await startPromptDebugBatchGroup({
      file: usingDb ? null : form.file,
      source: usingDb ? dbSource.value : '',
      itemIds: usingDb ? dbIds.value.join('\n') : '',
      systemPrompt: effectivePrompt.value,
      sheet: !usingDb && isXlsx.value ? form.sheet : '',
      // DB 模式的欄名由後端依來源註冊表決定，前端不送（送了也會被忽略）
      idColumn: usingDb ? '' : form.idColumn.trim(),
      textColumn: usingDb ? '' : form.textColumn.trim(),
      limit: form.limit,
      // 逐筆攤平：每筆自帶完整 provider + 旋鈕，故可以並排比「同 model 不同 effort」這種組合
      configs: selectedConfigs.value.map((c) => ({
        config_name: c.name,
        provider: c.provider,
        model: c.model,
        thinking: c.thinking,
        reasoning_effort: c.reasoning_effort,
        temperature: c.temperature,
      })),
    });
    groupMembers.value = result.members;
    activeGroupId.value = result.group_id;
    // ⚠️ 判「有沒有啟動成功」只看 `started`。這裡曾經讀 `m.ok`，而後端把 run 快照整包展開進
    // 成員、快照自帶的成功筆數（新 run 恆為 0）把布林旗標吃掉，於是 started 的成員全被判成失敗：
    // 成功訊息成了死碼、每個成功配置反而跳一則紅色「未知錯誤」、**群組輪詢從未啟動過**
    // （下面的 resumeGroupPoll 被擋在 if 後面），多模型並排進度對使用者從來沒真的運作。
    // 取數落差要主動講：貼了 1000 個 id 只跑到 940 筆，不講就只會看成「總數怎麼對不上」
    const s = result.db_stats;
    if (s && (s.missing || s.empty_conversations)) {
      Message.warning(
        `已取 ${s.valid_rows} / ${s.requested} 筆：查無資料 ${s.missing} 筆、對話內容為空 ${s.empty_conversations} 筆`,
      );
    }
    const started = result.members.filter((m) => m.started);
    const failed = result.members.filter((m) => !m.started);
    if (started.length) {
      Message.success(
        `跑批已啟動：${started.length} 個配置（${started.map((m) => m.config_name).join('、')}）`,
      );
    }
    for (const m of failed) {
      Message.error(`「${m.config_name}」啟動失敗：${m.error || '後端未回報原因，請看服務日誌'}`);
    }
    if (started.length) {
      resumeGroupPoll();
      void pollGroup();
    }
    await refreshRuns();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : '啟動跑批失敗');
  } finally {
    starting.value = false;
  }
}

async function onResume(run: PromptDebugBatchRunRow, rerun: boolean): Promise<void> {
  try {
    const snap = await resumePromptDebugBatchRun(run.run_id, { rerun });
    Message.success(
      rerun
        ? `重跑已啟動：目標 ${snap.total} 條全部重打`
        : `續跑已啟動：復用 ${snap.resumed} 條，待補 ${snap.pending} 條`,
    );
    activeSnap.value = snap;
    track(run.run_id);
    await refreshRuns();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : '啟動失敗');
  }
}

async function onCancel(runId: string): Promise<void> {
  try {
    await cancelPromptDebugBatchRun(runId);
    Message.info('已送出停止；已完成筆保留為斷點');
    if (runId === activeRunId.value) void pollActive();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : '停止失敗');
  }
}

async function onDownload(
  run: PromptDebugBatchRunRow,
  kind: PromptDebugBatchFileKind,
): Promise<void> {
  try {
    const blob = await downloadPromptDebugBatchFile(run.run_id, kind);
    const names: Record<PromptDebugBatchFileKind, string> = {
      csv: `${run.run_id}_results.csv`,
      preds: `${run.run_id}_preds.json`,
      input: run.input_name,
    };
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = names[kind];
    a.click();
    URL.revokeObjectURL(url);
  } catch (error) {
    Message.error(error instanceof Error ? error.message : '下載失敗');
  }
}

/**
 * 這一筆 run 能不能續跑／重跑。
 *
 * 只看**它自己**的狀態。過去用的是「清單裡任一筆在跑就全部禁用」，但各 run 各自獨立
 * （獨立 run 目錄、獨立 executor，後端也沒有這條限制），一筆在跑就鎖住其他所有筆是
 * 前端單方面加的枷鎖，讓「A 在跑的時候順手把 B 補完」這個很自然的操作做不到。
 */
const canResume = (record: PromptDebugBatchRunRow): boolean =>
  record.status !== 'running' && record.status !== 'cancelling';

// ── 跑批記錄的群組化呈現 ────────────────────────────────────────────────────────
//
// 多模型跑批＝同一份輸入 × 同一份 Prompt 在 N 個配置上各起一個獨立 run。攤平成 N 個平列時，
// 時間／輸入／Prompt 版本會一模一樣地重複 N 次，讀的人得自己比對哪幾列是同一批；而每個 model
// 的結果、狀態、續跑/重跑又必須各自獨立操作。所以：共用欄左側合併、每個 model 各佔一列。

/** 群組化後的顯示列（同群組相鄰，首列帶合併列數）。 */
interface GroupedRunRow extends PromptDebugBatchRunRow {
  /** 本群組共幾列（供 span-method 合併共用欄）。 */
  _groupSize: number;
  /** 是不是群組首列——只有首列回 rowspan，其餘由 Arco 自動移除。 */
  _groupFirst: boolean;
}

const groupedRuns = computed<GroupedRunRow[]>(() => {
  const order: string[] = [];
  const buckets: Record<string, PromptDebugBatchRunRow[]> = {};
  for (const run of runs.value) {
    // 單模型 run 沒有 group_id，各自成組（rowspan 1＝視覺上與改造前一致）
    const key = run.group_id || `solo:${run.run_id}`;
    if (!buckets[key]) {
      buckets[key] = [];
      order.push(key);
    }
    buckets[key].push(run);
  }
  return order.flatMap((key) =>
    buckets[key].map((run, index) => ({
      ...run,
      _groupSize: buckets[key].length,
      _groupFirst: index === 0,
    })),
  );
});

/** 群組內共用、要縱向合併的欄（值在同群組必然相同：同一份輸入、同一份 Prompt、同時發起）。
 *
 * 只有一欄：這三項資訊已收斂進單一「本批」描述區塊（見表格模板）。拆成三個窄欄時
 * 整表最小寬度要 1040px、必然橫向捲動，多模型群組的合併儲存格還會拉出一大塊空白。 */
const MERGED_COLUMNS = new Set(['created_at']);

/**
 * 共用欄的縱向合併：群組首列吃掉整組高度，其餘列的同名儲存格由 Arco 依 rowspan 自動移除
 * （`useSpan` 會自行記錄 removedCells，這裡**不需要**、也不該自己回 `{rowspan: 0}`）。
 */
function spanMethod(data: {
  record: TableData;
  column: TableColumnData | TableOperationColumn;
  rowIndex: number;
  columnIndex: number;
}): { rowspan?: number; colspan?: number } | void {
  // 操作欄（TableOperationColumn）沒有 dataIndex，`in` 收窄後自然被排除
  const dataIndex = 'dataIndex' in data.column ? data.column.dataIndex : undefined;
  if (!dataIndex || !MERGED_COLUMNS.has(dataIndex)) return;
  const record = data.record as GroupedRunRow;
  if (!record._groupFirst) return;
  return { rowspan: record._groupSize, colspan: 1 };
}

/**
 * 這一筆是否還有「續跑補得回來」的資料——沒有就不該出現「續跑」。
 *
 * ⚠️ 判準看的是**成功數**不是 `processed`。續跑的定義是「只補未成功筆」，而 `processed`
 * 把失敗筆也算進去了——用它會讓「跑完但有失敗筆」的 run 滿足 `processed === total`，
 * 續跑鈕被藏起來，可是那正是最需要續跑的情況。
 *
 * ⚠️⚠️ 而且**只能用 `ok_count`，不可再加 `resumed`**：後端 `_new_snapshot` 是
 * `"ok_count": resumed` 起算、再由 `_bump` 往上累加，也就是 `ok_count` **本身已含斷點復用的
 * 成功筆**（欄位註解寫明「累計成功筆數（含斷點復用）」）。兩者相加＝重複計算，會讓續跑過一次
 * 的 run（`resumed > 0`）即使還有失敗筆也剛好湊滿 `total` 而把按鈕藏掉——實際踩過。
 *
 * 後端 `resume_run` 不擋「無待補筆」，按下去會空轉起一條執行緒、重寫 summary.json、
 * 重置 started_at，看起來像做了什麼其實什麼也沒做。`total` 未知（磁碟推導）時保守顯示，
 * 因為那種情況確實需要靠續跑重算目標。
 */
const hasPending = (record: PromptDebugBatchRunRow): boolean =>
  record.total === null || record.ok_count < record.total;

watch(
  () => props.visible,
  async (visible) => {
    if (!visible) {
      pausePoll();
      pauseGroupPoll();
      return;
    }
    await refreshRuns();
    // 抽屜是 unmount-on-close，關掉就整組狀態歸零。若還有進行中的多模型群組，依 group_id
    // 把成員清單重建回來——否則關一次抽屜，並排比較的卡片就永久消失，只能回表格逐一點進度。
    const live = runs.value.filter((r) => r.status === 'running' || r.status === 'cancelling');
    const liveGroupId = live.find((r) => r.group_id)?.group_id;
    if (liveGroupId) {
      activeGroupId.value = liveGroupId;
      groupMembers.value = runs.value
        .filter((r) => r.group_id === liveGroupId)
        .map((r) => ({
          config_name: r.config_name || r.model,
          model: r.model,
          provider: '',
          started: true,
          run_id: r.run_id,
          status: r.status,
        }));
      resumeGroupPoll();
      void pollGroup();
    }
    if (live.length) track(live[0].run_id);
  },
);
</script>

<template>
  <a-drawer
    :visible="visible"
    :width="920"
    :footer="false"
    unmount-on-close
    @update:visible="emit('update:visible', $event)"
  >
    <template #title>跑批 · 以當前 Prompt 整批裁決</template>

    <!-- 本批固定配置摘要：啟動當下快照鎖進 run，之後編輯頁面不影響已建 run -->
    <a-alert type="info" class="mb-4">
      本批將鎖定啟動當下的配置：Prompt <b>{{ promptSummary }}</b> （<b>{{
        effectivePrompt.length.toLocaleString()
      }}</b>
      字元）；選幾個 model 就各自獨立起幾個
      run，互不影響、各自計費；併發由系統依模型自動調節（遇限流自動降速、回穩自動回升）；產物落在
      <code>data/prompt_debug_batch/&lt;run_id&gt;/</code>（jsonl 逐筆斷點，中斷可續跑）。
    </a-alert>

    <!-- 新跑批表單 -->
    <section class="mb-4 rounded-lg border border-[#e5e6eb] p-4">
      <!-- 版面尺度（**新增欄位請照這兩級填，不要再隨手給數字**）：
           · 區塊之間一律 `mb-3`（12px）；控件與其附註說明一律 `mt-1`（4px）
           · `a-col` 欄寬只有兩級——文字欄 `180px`、數字欄 `120px`；撐開占位用 `'auto'`
           改造前是 220/180/170/190/120/130 六個隨手值，欄與欄的視覺間距自然對不齊。 -->
      <div class="mb-3 text-sm font-semibold text-[#1d2129]">新跑批</div>

      <div class="mb-3 flex flex-col gap-1">
        <span class="text-xs text-[#4e5969]">Prompt 版本</span>
        <a-radio-group v-model="promptSource" type="button" size="small">
          <a-radio value="current">
            頁面當前內容（{{ systemPrompt.length.toLocaleString() }} 字元）
          </a-radio>
          <a-radio value="version">選擇已存檔版本</a-radio>
        </a-radio-group>
        <PromptVersionSelect
          v-if="promptSource === 'version'"
          v-model="versionKey"
          v-model:text="versionText"
          v-model:loading="loadingVersion"
          class="mt-1"
          :drafts="drafts"
          :releases="releases"
          placeholder="選擇要跑的版本（草稿或正式版）"
        />
      </div>

      <div class="mb-3 flex flex-col gap-1">
        <span class="text-xs text-[#4e5969]"
          >模型配置（可多選；同時跑幾個各自獨立比較，最多
          {{ MAX_CONFIGS_PER_GROUP }} 個。每筆自帶完整旋鈕，所以也能比「同一個 model 的不同
          effort」）</span
        >
        <LlmConfigSelect
          v-model="selectedConfigIds"
          multiple
          :limit="MAX_CONFIGS_PER_GROUP"
          :configs="configs"
          :provider-has-token="providerHasToken"
          placeholder="選擇一或多個模型配置"
        />
      </div>

      <div class="mb-3 flex flex-col gap-1">
        <span class="text-xs text-[#4e5969]">資料來源</span>
        <a-radio-group v-model="inputMode" type="button" size="small">
          <a-radio value="upload">上傳檔案</a-radio>
          <a-radio value="db">貼上 ID 從資料庫取</a-radio>
        </a-radio-group>
      </div>

      <!-- DB 取數：選反饋來源 + 貼一串自然鍵，欄名由後端依來源註冊表決定 -->
      <template v-if="inputMode === 'db'">
        <a-row :gutter="[12, 8]" align="center" wrap class="mb-3">
          <a-col :flex="'180px'">
            <div class="flex flex-col gap-1">
              <span class="text-xs text-[#4e5969]">反饋來源</span>
              <a-select v-model="dbSource" class="w-full" size="small" :options="SOURCE_OPTS" />
            </div>
          </a-col>
          <a-col :flex="'auto'" class="self-end">
            <span class="text-xs text-[#86909c]">
              已辨識
              <b :class="dbIds.length ? 'text-[#00b42a]' : 'text-[#86909c]'">{{ dbIds.length }}</b>
              個 ID（自動去重）
            </span>
          </a-col>
        </a-row>
        <a-textarea
          v-model="dbIdsText"
          :auto-size="{ minRows: 5, maxRows: 12 }"
          placeholder="每行一個 ID（也可用逗號或空白分隔），例如 session_oid：&#10;717255&#10;668295&#10;709342"
          allow-clear
        />
        <div class="mt-1 text-xs text-[#86909c]">
          後端會依所選來源撈出對話原文並固定成本批快照（續跑重放的是同一份文字，不受來源表之後被覆蓋影響）；查無資料或內容為空的
          ID 會被跳過並在啟動後回報筆數。
        </div>
      </template>

      <a-upload
        v-show="inputMode === 'upload'"
        v-model:file-list="fileList"
        :auto-upload="false"
        :limit="1"
        accept=".csv,.xlsx,.xlsm"
        draggable
        @change="onFileChange"
      >
        <template #upload-button>
          <div
            class="flex h-20 w-full cursor-pointer flex-col items-center justify-center rounded-lg border border-dashed border-[#c9cdd4] bg-[#f7f8fa] text-sm text-[#4e5969]"
          >
            <div>點擊或拖入輸入檔（.csv / .xlsx）</div>
            <div class="mt-1 text-xs text-[#86909c]">
              需含唯一 ID 欄與完整對話欄；表頭行自動識別（前 20 行內）
            </div>
          </div>
        </template>
        <!-- 自繪 item：Arco 預設 item 會渲染一顆對本元件無效的「開始上傳」play 圖示（見 fileList 註解） -->
        <template #upload-item="{ fileItem }">
          <div class="mt-2 flex items-center gap-2 rounded border border-[#e5e6eb] px-3 py-2">
            <icon-file class="shrink-0 text-[#86909c]" />
            <span class="truncate text-sm text-[#1d2129]">
              {{ fileItem.name || fileItem.file?.name }}
            </span>
            <a-button
              class="ml-auto shrink-0"
              type="text"
              size="mini"
              status="danger"
              @click="clearFile"
            >
              <template #icon><icon-delete /></template>
            </a-button>
          </div>
        </template>
      </a-upload>

      <!-- 與上方「資料來源」區塊之間用分隔線斷開：這列是跑批範圍參數、不屬資料來源，
           先前只靠 mt-3 貼在上傳框／說明文字下方，視覺上像是資料來源區塊的一部分 -->
      <a-row :gutter="[12, 8]" align="center" wrap class="mt-4 border-t border-[#e5e6eb] pt-4">
        <!-- 欄名/工作表只描述「上傳檔長什麼樣」，DB 取數時由來源註冊表決定，不該讓人填 -->
        <a-col v-if="inputMode === 'upload' && isXlsx" :flex="'180px'">
          <div class="flex flex-col gap-1">
            <span class="text-xs text-[#4e5969]">工作表（空＝第一個）</span>
            <a-input v-model="form.sheet" class="w-full" placeholder="Sheet 名" allow-clear />
          </div>
        </a-col>
        <a-col v-if="inputMode === 'upload'" :flex="'180px'">
          <div class="flex flex-col gap-1">
            <span class="text-xs text-[#4e5969]">ID 欄名</span>
            <a-input v-model="form.idColumn" class="w-full" />
          </div>
        </a-col>
        <a-col v-if="inputMode === 'upload'" :flex="'180px'">
          <div class="flex flex-col gap-1">
            <span class="text-xs text-[#4e5969]">對話欄名</span>
            <a-input v-model="form.textColumn" class="w-full" />
          </div>
        </a-col>
        <a-col :flex="'120px'">
          <div class="flex flex-col gap-1">
            <span class="text-xs text-[#4e5969]">limit（0＝全部）</span>
            <a-input-number v-model="form.limit" class="w-full" :min="0" :step="10" />
          </div>
        </a-col>
        <a-col :flex="'auto'" class="self-end text-right">
          <a-button type="primary" :loading="starting" :disabled="!canStart" @click="onStart">
            <template #icon><icon-play-arrow /></template>
            開始跑批
          </a-button>
        </a-col>
      </a-row>
      <div class="mt-1 text-xs text-[#86909c]">
        每次啟動建立新 run；跑到一半可停止，之後在下方記錄「續跑」只補未成功筆。全量大批請先用小
        limit 試跑確認欄位與 Prompt 再放量。
      </div>
    </section>

    <!-- 多模型群組總覽：每個 model 一列獨立進度；要看某個 model 的完整明細（recent/warnings/
         失敗清單）點右側「詳情」切到下方單 run 追蹤區，兩者不重複做一套 UI -->
    <section v-if="groupOverview.length" class="mb-4 rounded-lg border border-[#e5e6eb] p-4">
      <div class="mb-2 text-sm font-semibold text-[#1d2129]">
        本次跑批 · {{ groupOverview.length }} 個 model
      </div>
      <div class="flex flex-col gap-2">
        <div
          v-for="{ member, run } in groupOverview"
          :key="member.config_name"
          class="rounded border border-[#e5e6eb] p-2"
        >
          <div class="flex items-center justify-between gap-2">
            <div class="flex items-center gap-2 text-sm">
              <span class="font-medium">{{ member.config_name }}</span>
              <span class="text-xs text-[#86909c]">{{ member.model }} · {{ member.provider }}</span>
              <a-tag v-if="!member.started" color="red" size="small">啟動失敗</a-tag>
              <a-tag v-else :color="statusMeta(run?.status ?? 'running').color" size="small">
                {{ statusMeta(run?.status ?? 'running').label }}
              </a-tag>
            </div>
            <a-button v-if="member.run_id" type="text" size="mini" @click="track(member.run_id)"
              ><template #icon><icon-eye /></template>詳情</a-button
            >
          </div>
          <div v-if="!member.started" class="mt-1 text-xs text-[#f53f3f]">
            {{ member.error || '後端未回報原因，請看服務日誌' }}
          </div>
          <template v-else-if="run">
            <a-progress
              class="mt-1"
              :percent="run.total ? Math.min(1, (run.resumed + run.processed) / run.total) : 0"
              :show-text="false"
              size="small"
            />
            <div class="mt-1 flex flex-wrap gap-x-3 text-xs text-[#4e5969]">
              <span>{{ run.processed }} / {{ run.total || '—' }}</span>
              <span class="text-[#00b42a]">成功 {{ run.ok_count }}</span>
              <span v-if="run.failed" class="text-[#f53f3f]">失敗 {{ run.failed }}</span>
              <span>US$ {{ run.cost_usd.toFixed(4) }}</span>
            </div>
          </template>
        </div>
      </div>
    </section>

    <!-- 進行中／最近追蹤 run 的即時進度 -->
    <section v-if="activeSnap" class="mb-4 rounded-lg border border-[#e5e6eb] p-4">
      <div class="mb-2 flex items-center justify-between gap-3">
        <div class="flex items-center gap-2 text-sm font-semibold text-[#1d2129]">
          進度 · {{ activeSnap.run_id }}
          <a-tag :color="statusMeta(activeSnap.status).color" size="small">
            {{ statusMeta(activeSnap.status).label }}
          </a-tag>
        </div>
        <a-popconfirm
          v-if="activeSnap.status === 'running'"
          content="確定停止？已完成筆保留為斷點，之後可續跑。"
          @ok="onCancel(activeSnap.run_id)"
        >
          <a-button size="small" status="danger"><template #icon><icon-record-stop /></template>停止</a-button>
        </a-popconfirm>
      </div>

      <a-progress
        v-if="pct !== null"
        :percent="pct"
        :status="activeSnap.status === 'error' ? 'danger' : pct >= 1 ? 'success' : 'normal'"
      >
        <template #text="{ percent }">{{ fmtPercent(percent) }}</template>
      </a-progress>
      <!-- total 未知（重啟後磁碟推導）：不畫 0% 假進度，明講已完成筆數、其餘待續跑重算 -->
      <a-alert v-else type="normal" class="mb-2">
        這個 run 在服務重啟後才被讀取，當時的目標筆數已無從得知；下方數字為斷點檔實際累計。
        按「續跑」會重新計算目標並補完未跑的筆。
      </a-alert>
      <div class="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-[#4e5969]">
        <span>目標 {{ activeSnap.total ?? '—' }}</span>
        <span>斷點復用 {{ activeSnap.resumed }}</span>
        <span class="text-[#00b42a]">成功 {{ activeSnap.ok_count }}</span>
        <span :class="activeSnap.failed ? 'text-[#f53f3f]' : ''">失敗 {{ activeSnap.failed }}</span>
        <span :class="activeSnap.invalid ? 'text-[#ff7d00]' : ''">
          校驗未過 {{ activeSnap.invalid }}
        </span>
        <span>{{ activeSnap.total_tokens.toLocaleString() }} tokens</span>
        <span>US$ {{ activeSnap.cost_usd.toFixed(4) }}</span>
        <span v-if="etaText">{{ etaText }}</span>
      </div>

      <a-alert v-if="activeSnap.error" type="error" class="mt-3">{{ activeSnap.error }}</a-alert>
      <a-alert v-for="w in activeSnap.warnings" :key="w" type="warning" class="mt-2">{{
        w
      }}</a-alert>

      <div v-if="activeSnap.recent.length" class="recent-list mt-3">
        <div
          v-for="item in activeSnap.recent"
          :key="item.item_id + String(item.latency_ms)"
          class="recent-row"
        >
          <span :class="item.succeeded ? 'text-[#00b42a]' : 'text-[#f53f3f]'">
            {{ item.succeeded ? '✓' : '✗' }}
          </span>
          <span class="font-medium">{{ item.item_id }}</span>
          <span v-if="item.succeeded" class="truncate text-[#4e5969]">
            {{ item.L1 }} › {{ item.L2 }}
            <template v-if="item.issues">（校驗未過 {{ item.issues }} 項）</template>
          </span>
          <span v-else class="truncate text-[#f53f3f]">{{ item.error }}</span>
          <span class="ml-auto shrink-0 text-[#86909c]">
            {{ item.latency_ms != null ? `${(item.latency_ms / 1000).toFixed(1)}s` : '—' }}
          </span>
        </div>
      </div>

      <a-collapse v-if="activeSnap.failed_items.length" class="mt-3" :bordered="false">
        <a-collapse-item
          key="failed"
          :header="`失敗明細 ${activeSnap.failed_items.length}${activeSnap.failed_items_truncated ? '+' : ''} 筆`"
        >
          <div v-for="f in activeSnap.failed_items" :key="f.item_id" class="text-xs text-[#4e5969]">
            <span class="font-medium">{{ f.item_id }}</span
            >：{{ f.error }}
          </div>
        </a-collapse-item>
      </a-collapse>
    </section>

    <!-- 跑批記錄（磁碟 run 目錄為準；server 重啟後仍在，可續跑） -->
    <section class="rounded-lg border border-[#e5e6eb] p-4">
      <div class="mb-2 flex items-center justify-between gap-3">
        <div class="text-sm font-semibold text-[#1d2129]">跑批記錄</div>
        <a-button size="small" type="text" :loading="loadingRuns" @click="refreshRuns">
          <template #icon><icon-refresh /></template>
          刷新
        </a-button>
      </div>
      <!-- 三態的 error：不遮資料（載入失敗時舊清單仍可用），但要持續可見到下次成功刷新為止。 -->
      <a-alert v-if="runsError" type="error" class="mb-2">{{ runsError }}</a-alert>
      <a-table
        :data="groupedRuns"
        :loading="loadingRuns"
        :pagination="false"
        size="small"
        row-key="run_id"
        :span-method="spanMethod"
        table-layout-fixed
      >
        <template #columns>
          <!-- 「本批」＝整組共用的資訊（同一份輸入 × 同一份 Prompt × 同時發起），
               多模型群組只顯示一次（span-method 縱向合併 created_at 這一欄）。
               ⚠️ 刻意收成單一描述區塊而非三個窄欄：拆欄會讓整表最小寬度到 1040px、必然橫向捲動，
               而抽屜寬度有限；合併儲存格也會因為鄰欄有 N 列而拉出一大塊空白（見 RecordContextPanel
               的 compact 版型，同一套「左小標籤＋右內容」語彙）。
               ⚠️ 「本批」「執行」兩欄**刻意都不給 `width`**：配合 `table-layout-fixed`，未指定寬度的
               欄會等分剩餘空間。給了 px 反而兩頭不討好——220 太窄會把時間戳從中間折行，300 又讓
               「執行」欄從單字中間斷開；等分在各種抽屜寬度下都成立。「操作」欄則相反，固定窄寬
               （見該欄註解）。 -->
          <!-- ⚠️ 只有這一欄垂直置中：它是**整組的標籤**（rowspan 吃掉 N 列高度），置中才與整組對齊；
               靠上會讓它黏在第一個 model 那列、下方拖一大塊空白。「執行」「操作」是逐列內容，
               必須靠上與各自那一列對齊——兩者語義不同，對齊方式刻意不一致。 -->
          <a-table-column
            title="本批"
            data-index="created_at"
            :cell-style="{ verticalAlign: 'middle' }"
          >
            <template #cell="{ record }">
              <div class="flex flex-col gap-1">
                <div class="flex items-center gap-1.5">
                  <span class="whitespace-nowrap text-xs font-medium">
                    {{ fmtBeijingDt(record.created_at) }}
                  </span>
                  <a-tag v-if="record.group_id" size="small" color="arcoblue">
                    多模型 · {{ record._groupSize }}
                  </a-tag>
                </div>
                <div class="flex gap-1.5 text-[11px] leading-[1.6]">
                  <span class="shrink-0 text-[#86909c]">輸入</span>
                  <span class="min-w-0 truncate text-[#4e5969]" :title="record.input_name">
                    {{ record.input_name }}
                  </span>
                </div>
                <div class="flex gap-1.5 text-[11px] leading-[1.6]">
                  <span class="shrink-0 text-[#86909c]">範圍</span>
                  <span class="min-w-0 text-[#4e5969]">
                    limit {{ record.limit || '全部' }} · 最高 {{ record.workers ?? '—' }} 併發
                  </span>
                </div>
                <div class="flex items-center gap-1.5 text-[11px] leading-[1.6]">
                  <span class="shrink-0 text-[#86909c]">Prompt</span>
                  <a-tag size="small" :color="record.prompt_version ? 'arcoblue' : 'orange'">
                    {{ record.prompt_version || '臨時編輯版' }}
                  </a-tag>
                  <!-- 口徑軌跡：後端一直有送 prompt_kind，前端過去完全沒顯示——
                       「那批到底是拿正式版還是草稿跑的」正是事後對帳最常問的問題。 -->
                  <span v-if="record.prompt_kind" class="text-[#86909c]">
                    {{ record.prompt_kind }}
                  </span>
                </div>
              </div>
            </template>
          </a-table-column>

          <!-- 「執行」＝逐 model 各自一列：模型／結果／狀態／耗時同樣收成一個描述區塊 -->
          <a-table-column title="執行" :cell-style="{ verticalAlign: 'top' }">
            <template #cell="{ record }">
              <div class="flex flex-col gap-1">
                <div class="flex flex-wrap items-center gap-x-2 gap-y-1">
                  <!-- break-keep：配置名是「供應商 · model · 檔位」的組合，從單字中間斷開
                       （`m / edium`）比換行更難讀；容器窄時整段換行即可。 -->
                  <span class="break-keep text-xs font-medium">
                    {{ record.config_name || record.model }}
                  </span>
                  <a-tag size="small" :color="statusMeta(record.status).color">
                    {{ statusMeta(record.status).label }}
                  </a-tag>
                  <span class="text-[11px] text-[#86909c]">
                    {{ fmtDurationSec(record.elapsed_total_sec) }}
                    <a-tooltip
                      v-if="record.session_count > 1"
                      :content="`分 ${record.session_count} 段執行（中途停過再續跑）；本段 ${fmtDurationSec(record.elapsed_sec)}`"
                    >
                      <span>· {{ record.session_count }} 段</span>
                    </a-tooltip>
                  </span>
                </div>
                <div class="flex flex-wrap items-center gap-x-2 text-[11px] leading-[1.6]">
                  <span class="text-[#00b42a]">成功 {{ record.ok_count }}</span>
                  <span v-if="record.failed" class="text-[#f53f3f]">敗 {{ record.failed }}</span>
                  <span class="text-[#86909c]">/ {{ record.total ?? '—' }}</span>
                  <span class="text-[#86909c]">US$ {{ record.cost_usd.toFixed(4) }}</span>
                </div>
                <div class="flex gap-1.5 text-[11px] leading-[1.6] text-[#c9cdd4]">
                  <span v-if="record.config_name" class="shrink-0">{{ record.model }}</span>
                  <span class="min-w-0 truncate" :title="record.run_id">{{ record.run_id }}</span>
                </div>
              </div>
            </template>
          </a-table-column>
          <!-- ⚠️ 「操作」欄刻意固定窄寬 + 按鈕直排：這一列的動作最多 3 顆（看進度/停止、續跑、重跑、
               CSV），橫排時得吃掉整表 1/3 寬度，而左邊兩欄的描述區塊本來就更需要空間。直排不會
               增加列高——同列的「執行」描述區塊本身就是 3 行高，按鈕正好落在同一段垂直空間內。 -->
          <!-- 100 是實測下限：最寬的一列「重跑 · CSV」（icon + 2 字 + 分隔 + 3 字）需 65px，
               加 Arco 預設左右內距 32px ＝ 97，取 100 留 3px 餘裕。
               ⚠️ 2026-08-07 踩過：本輪替按鈕加 icon 後沒重量寬度，96 讓該列被切掉 1px（肉眼幾乎看不出，
               但字會缺角）。改按鈕文案或 icon 後一律照 `.claude/rules/frontend-vue.md` 用瀏覽器重量。 -->
          <a-table-column title="操作" :width="100" :cell-style="{ verticalAlign: 'top' }">
            <template #cell="{ record }">
              <div class="flex flex-col items-start gap-y-0.5">
                <a-button
                  v-if="record.status === 'running' || record.status === 'cancelling'"
                  size="mini"
                  type="text"
                  @click="track(record.run_id)"
                  ><template #icon><icon-eye /></template>看進度</a-button
                >
                <a-popconfirm
                  v-if="record.status === 'running'"
                  content="確定停止？已完成筆保留為斷點。"
                  @ok="onCancel(record.run_id)"
                >
                  <a-button size="mini" type="text" status="danger"><template #icon><icon-record-stop /></template>停止</a-button>
                </a-popconfirm>
                <template v-if="record.status !== 'running' && record.status !== 'cancelling'">
                  <a-button
                    v-if="hasPending(record)"
                    size="mini"
                    type="text"
                    :disabled="!canResume(record)"
                    @click="onResume(record, false)"
                    ><template #icon><icon-play-arrow /></template>續跑</a-button
                  >
                  <a-popconfirm
                    content="忽略斷點、全部重打（重新計費），確定？"
                    @ok="onResume(record, true)"
                  >
                    <a-button
                      size="mini"
                      type="text"
                      status="warning"
                      :disabled="!canResume(record)"
                      ><template #icon><icon-sync /></template>重跑</a-button
                    >
                  </a-popconfirm>
                </template>
                <a-button
                  v-if="record.has_csv"
                  size="mini"
                  type="text"
                  @click="onDownload(record, 'csv')"
                >
                  <template #icon><icon-download /></template>
                  CSV
                </a-button>
              </div>
            </template>
          </a-table-column>
        </template>
        <template #empty>
          <a-empty description="尚無跑批記錄；上方上傳輸入檔開始第一批" />
        </template>
      </a-table>
    </section>
  </a-drawer>
</template>

<style scoped>
.recent-list {
  max-height: 180px;
  overflow: auto;
  border: 1px solid #e5e6eb;
  border-radius: 8px;
  padding: 6px 10px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 12px;
  line-height: 1.7;
}
.recent-row {
  display: flex;
  gap: 8px;
  align-items: baseline;
  min-width: 0;
}
</style>

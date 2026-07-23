// Prompt 測試沙盒「執行 + 結果檢視」：呼叫 start API → 開執行日誌 SSE 即時串流 → 輪詢至終態 →
// 正規化落庫結果。runSeq token 機制防止「舊一輪測試」的輪詢在關閉/新一輪測試後續跑覆寫畫面
// （見 invalidate()）。activeRun/runCompare/logEntries 亦是「測試歷史」查看/對比時共用的顯示
// 狀態（usePromptSandboxHistory 抓到資料後由呼叫端 PromptSandboxDrawer 寫回這裡的 ref，避免兩份
// 平行狀態）。
import { ref, toValue, type MaybeRefOrGetter, type Ref } from 'vue';
import { Message } from '@arco-design/web-vue';
import {
  getPromptSandboxRun,
  getPromptSandboxStatus,
  prejudgeLogStreamUrl,
  startPromptSandbox,
  type PromptSandboxItemResult,
  type PromptSandboxRunCompare,
  type PromptSandboxRunSummary,
  type PromptSandboxStartBody,
  type SandboxCompareMetrics,
} from '@/api';
import { getRuleDraft } from '@/api/judgeRules.api';
import type { LogEntry } from '../components/PrejudgeLogView.types';

/** 一次沙盒測試 run 的完整詳情（結果 + 執行日誌 + 草稿快照 / 雙跑等價性 metrics）。 */
export interface SandboxRunDetail extends PromptSandboxRunSummary {
  results: PromptSandboxItemResult[];
  log: LogEntry[];
  /** 草稿測試 run：各 prompt 的草稿 md 全文快照（採納入庫的內容來源）。 */
  drafts?: Record<string, string>;
  /** 雙跑對比 run：baseline vs draft 等價性聚合（後端讀取時動態算）。 */
  metrics?: SandboxCompareMetrics | null;
}

/** 防禦舊資料：歷史 run 的 log/results 可能為 null（舊 schema 落庫），v-for 迭代 null 會讓整個
 * 抽屜 render 掛掉（實測全白）——載入點統一補空陣列。 */
export function normalizeSandboxRun(r: SandboxRunDetail): SandboxRunDetail {
  r.results = r.results ?? [];
  r.log = r.log ?? [];
  for (const item of r.results) item.prompts = item.prompts ?? [];
  return r;
}

/** usePromptSandboxJob 的注入依賴。 */
interface PromptSandboxJobDeps {
  /** 觸發入口：single＝單列按鈕；all＝工具列批量（見 usePromptSandboxTargets）。 */
  scope: MaybeRefOrGetter<'single' | 'all'>;
  /** 當前反饋來源 code。 */
  source: MaybeRefOrGetter<string>;
  /** scope=single 時的受測 source_id 清單。 */
  sourceIds: MaybeRefOrGetter<string[]>;
  /** 納入測試的 prompt 端點值清單（polarity/C-1..C-6；由勾選 rule_code 轉換而來）。 */
  promptArgs: MaybeRefOrGetter<string[]>;
  /** 逐支 prompt 指定版本（PromptVersionPickerGroup resolved）。 */
  versionSelection: MaybeRefOrGetter<{ versions: Record<string, number> }>;
  /** 納入測試且處於草稿模式的 rule_code 清單。 */
  draftCodes: MaybeRefOrGetter<string[]>;
  /** 有草稿時是否雙跑對比（關＝只跑草稿省 token 但無前後對照）。 */
  compareEnabled: MaybeRefOrGetter<boolean>;
  /** 本次執行 LLM 覆寫（provider + 旋鈕，來自 useLlmAreaDefault('sandbox')）。 */
  overrides: MaybeRefOrGetter<PromptSandboxStartBody['overrides']>;
  /** scope='all' 時組請求 body（來自 usePromptSandboxTargets）。 */
  scopeBody: (promptIds: string[]) => PromptSandboxStartBody;
  /** rule_code → 顯示標籤（草稿缺漏時的錯誤訊息用）。 */
  labelFor: (code: string) => string;
  /** 確認即收面板：測試結果/執行日誌立即可見（僅通過參數驗證後才收合，驗證失敗維持面板開啟）。 */
  settingsOpen: Ref<boolean>;
  /** 開始測試即切到結果分頁（同上，僅驗證通過後生效）。 */
  activeTab: Ref<'results' | 'log' | 'history'>;
  /** 測試完成寫入 activeRun 後呼叫（呼叫端用來刷新測試歷史列表）；於 `running` 復位前 await
   *  完成，與原邏輯執行順序一致。 */
  onDone?: () => Promise<void> | void;
}

/**
 * Prompt 測試沙盒的執行任務：啟動測試 → SSE 即時串流執行日誌 → 輪詢至終態 → 正規化落庫結果。
 * 亦持有「當前顯示的結果」狀態（activeRun/runCompare/logEntries），供測試歷史查看 / 對比複用
 * 同一份顯示位置（見 usePromptSandboxHistory 的呼叫端如何寫回這些 ref）。
 * @returns 執行狀態（running/logStreaming）、當前顯示結果（activeRun/runCompare/logEntries）、
 *   `run` 啟動一次測試、`closeLogStream` 供切換顯示前主動關閉即時串流、`invalidate` 作廢進行中輪詢。
 */
export function usePromptSandboxJob(deps: PromptSandboxJobDeps) {
  const running = ref(false);
  const activeRun = ref<SandboxRunDetail | null>(null);
  /** run-vs-run 對比檢視（非 null 時結果分頁顯示對比而非單次 run）。 */
  const runCompare = ref<PromptSandboxRunCompare | null>(null);

  // ── 執行日誌（跑測試時 SSE 即時串流；完成/回看歷史時改顯示落庫的權威 log 快照）──
  const logEntries = ref<LogEntry[]>([]);
  const logStreaming = ref(false);
  let logEs: EventSource | null = null;
  const closeLogStream = (): void => {
    logEs?.close();
    logEs = null;
    logStreaming.value = false;
  };
  const openLogStream = (jobId: string): void => {
    closeLogStream();
    logEntries.value = [];
    logStreaming.value = true;
    logEs = new EventSource(prejudgeLogStreamUrl(jobId));
    logEs.onopen = () => {
      logEntries.value = []; // 自動重連會整批重放 → 先清空避免重複
    };
    logEs.onmessage = (ev) => logEntries.value.push(JSON.parse(ev.data));
    logEs.addEventListener('done', () => closeLogStream());
    logEs.addEventListener('error', (ev) => {
      // 僅後端明確推送的 error event（帶 data）才終止；原生連線瞬斷無 data → 交給自動重連
      // （瞬斷即關流會讓日誌永遠空白）。
      if ((ev as MessageEvent).data) closeLogStream();
    });
  };

  // 輪詢世代 token：抽屜關閉/元件卸載/新一輪測試時遞增作廢舊迴圈——舊 run() 的輪詢在下一次
  // await 醒來後發現 token 過期即靜默退出，不再打 API、不覆寫使用者當下畫面（如切去看的
  // 歷史紀錄）、也不動新一輪的 running 狀態。
  let runSeq = 0;
  /** 作廢進行中的輪詢迴圈（抽屜關閉 / 元件卸載時呼叫）。 */
  const invalidate = (): void => {
    runSeq += 1;
  };

  /** 啟動一次沙盒測試：呼叫 start API → 開執行日誌串流 → 輪詢至終態 → 正規化落庫結果寫入
   * activeRun，成功後呼叫 `deps.onDone`。 */
  const run = async (): Promise<void> => {
    if (!toValue(deps.promptArgs).length) {
      Message.warning('請至少勾選一支 Prompt');
      return;
    }
    if (toValue(deps.scope) === 'single' && !toValue(deps.sourceIds).length) {
      Message.warning('沒有受測項目');
      return;
    }
    const token = ++runSeq;
    running.value = true;
    activeRun.value = null;
    runCompare.value = null; // 新一輪測試離開 run-vs-run 檢視
    deps.settingsOpen.value = false;
    deps.activeTab.value = 'results';
    try {
      const promptArgs = toValue(deps.promptArgs);
      const scope = toValue(deps.scope);
      const body: PromptSandboxStartBody =
        scope === 'all'
          ? deps.scopeBody(promptArgs)
          : {
              source: toValue(deps.source),
              item_ids: toValue(deps.sourceIds),
              prompt_ids: promptArgs,
              scope,
            };
      body.overrides = toValue(deps.overrides);
      const versions = toValue(deps.versionSelection).versions;
      if (Object.keys(versions).length) {
        body.versions = versions;
      }
      // 草稿模式：送測時取 DB 草稿內容快照帶入（後端逐條強驗 + 落庫快照，與草稿後續演進脫鉤）
      const draftCodes = toValue(deps.draftCodes);
      if (draftCodes.length) {
        const fetched = await Promise.all(
          draftCodes.map(async (code) => ({ code, ...(await getRuleDraft(code)) })),
        );
        const drafts: Record<string, string> = {};
        for (const { code, draft } of fetched) {
          const text = typeof draft?.content.text === 'string' ? draft.content.text : '';
          if (!text.trim()) {
            throw new Error(`「${deps.labelFor(code)}」草稿不存在或為空，請先編輯儲存`);
          }
          drafts[code] = text;
        }
        body.drafts = drafts;
        body.compare = toValue(deps.compareEnabled);
      }
      const { job_id } = await startPromptSandbox(body);
      if (token !== runSeq) return;
      openLogStream(job_id); // 執行日誌分頁即時串流（與輪詢並行，互不影響）
      // 輪詢至終態（done/error）；沙盒非長批次，短間隔即可即時反映進度。
      while (true) {
        await new Promise((r) => setTimeout(r, 700));
        if (token !== runSeq) return;
        const snap = await getPromptSandboxStatus(job_id);
        if (token !== runSeq) return;
        if (snap.status === 'done' && snap.run_id) {
          const detail = normalizeSandboxRun(await getPromptSandboxRun(snap.run_id));
          if (token !== runSeq) return;
          activeRun.value = detail;
          closeLogStream();
          logEntries.value = detail.log; // 改顯示落庫的權威快照（避免 SSE 重連/漏幀差異）
          await deps.onDone?.();
          break;
        }
        if (snap.status === 'error') {
          closeLogStream();
          Message.error('測試任務失敗');
          break;
        }
      }
    } catch (e) {
      if (token === runSeq) Message.error(e instanceof Error ? e.message : '測試失敗');
    } finally {
      if (token === runSeq) running.value = false;
    }
  };

  return {
    running,
    activeRun,
    runCompare,
    logEntries,
    logStreaming,
    run,
    closeLogStream,
    invalidate,
  };
}

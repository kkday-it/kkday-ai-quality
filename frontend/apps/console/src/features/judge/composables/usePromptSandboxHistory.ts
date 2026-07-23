// Prompt 測試沙盒的測試歷史（與正式初判歷史完全分離）：歷史列表載入、查看單筆詳情、勾選兩筆
// run-vs-run 對比。取得的資料如何顯示（寫入 usePromptSandboxJob 的 activeRun/runCompare/
// logEntries）由呼叫端 PromptSandboxDrawer 決定——本檔只管抓資料 + 錯誤處理，避免與 job composable
// 互相依賴（viewHistoryRun/doCompareRuns 皆以 callback 交還結果，只在成功時被呼叫）。
import { ref } from 'vue';
import { Message } from '@arco-design/web-vue';
import {
  comparePromptSandboxRuns,
  getPromptSandboxRun,
  listPromptSandboxRuns,
  type PromptSandboxRunCompare,
  type PromptSandboxRunSummary,
} from '@/api';
import { normalizeSandboxRun, type SandboxRunDetail } from './usePromptSandboxJob';

/** 範圍中文標籤（歷史列表用）。selection 為舊版「已選 N」按鈕遺留的歷史紀錄值（該按鈕已併入
 * all 的「已選內」子模式，觸發端不再產生新的 selection，此處僅為相容顯示舊資料）。 */
export const SANDBOX_SCOPE_LABEL: Record<string, string> = {
  single: '單列',
  selection: '選取',
  all: '批量',
};

/**
 * Prompt 測試沙盒的測試歷史：列表載入、查看單筆詳情、勾兩筆 run-vs-run 對比。
 * @returns 歷史列表狀態（history/historyLoading）+ `loadHistory` 刷新 + 對比勾選狀態
 *   （compareSelection/comparing）+ `viewHistoryRun`/`doCompareRuns`（取得資料後透過 callback
 *   交還呼叫端寫入顯示狀態）。
 */
export function usePromptSandboxHistory() {
  const history = ref<PromptSandboxRunSummary[]>([]);
  const historyLoading = ref(false);
  async function loadHistory(): Promise<void> {
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

  /** 查看某次歷史測試：拉完整詳情（含 results + log 快照）並交給 `onLoaded` 顯示；呼叫端須自行
   * 在呼叫前關閉即時串流 / 離開 run-vs-run 檢視（與原邏輯執行順序一致，見 PromptSandboxDrawer 的
   * `viewHistoryRun` wrapper）。 */
  async function viewHistoryRun(
    runId: string,
    onLoaded: (detail: SandboxRunDetail) => void,
  ): Promise<void> {
    try {
      const detail = normalizeSandboxRun(await getPromptSandboxRun(runId));
      onLoaded(detail);
    } catch (e) {
      Message.error(e instanceof Error ? e.message : '載入測試紀錄失敗');
    }
  }

  /** 測試歷史勾恰兩筆 → run-vs-run 對比（後端按 source_id 對齊 + metrics）。 */
  const compareSelection = ref<string[]>([]);
  const comparing = ref(false);
  async function doCompareRuns(
    onCompared: (compare: PromptSandboxRunCompare) => void,
  ): Promise<void> {
    if (compareSelection.value.length !== 2) return;
    comparing.value = true;
    try {
      const [a, b] = compareSelection.value;
      const compare = await comparePromptSandboxRuns(a, b);
      onCompared(compare);
    } catch (e) {
      Message.error(e instanceof Error ? e.message : '對比失敗');
    } finally {
      comparing.value = false;
    }
  }

  return {
    history,
    historyLoading,
    loadHistory,
    compareSelection,
    comparing,
    viewHistoryRun,
    doCompareRuns,
  };
}

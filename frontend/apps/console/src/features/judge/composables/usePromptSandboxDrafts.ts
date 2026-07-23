// Prompt 測試沙盒的草稿閉環（編輯 → 測試 → 對比 → 入庫）UI 協調：草稿編輯抽屜、採納入庫確認
// 抽屜的開關與資料組裝，以及存檔/入庫後刷新 PromptVersionPickerGroup 的草稿/版本選項。
// 不含「哪些 rule_code 納入草稿模式測試」（draftCodes/compareEnabled）——那是
// PromptVersionPickerGroup 直接 emit 給 PromptSandboxDrawer 的送測參數，與本檔的抽屜協調職責
// 分離，繼續留在呼叫端（比照 selectedCodes/versionSelection 的既有模式）。
import { ref, type Ref } from 'vue';
import { Message } from '@arco-design/web-vue';
import type { SandboxRunDetail } from './usePromptSandboxJob';

/** PromptVersionPickerGroup 對外方法子集（僅草稿閉環用到的部分，面向介面而非元件實作型別，
 * 避免 composable 反向耦合元件）。 */
export interface PromptVersionPickerHandle {
  refreshDrafts: () => Promise<void>;
  reloadHistory: (code: string) => Promise<void>;
  activeVersionOf: (code: string) => number | undefined;
}

/** usePromptSandboxDrafts 的注入依賴。 */
interface PromptSandboxDraftsDeps {
  /** 當前顯示的測試 run（讀取草稿快照供採納入庫用；唯讀消費，由 usePromptSandboxJob 提供）。 */
  activeRun: Ref<SandboxRunDetail | null>;
}

/**
 * Prompt 測試沙盒的草稿閉環：草稿編輯抽屜、採納入庫確認抽屜的開關與資料組裝。
 * @returns `pickerRef`（綁定 PromptVersionPickerGroup 模板 ref）+ 抽屜狀態
 *   （draftEditor/adopt）+ 開啟動作（openDraftEditor/openAdopt）+ picker 刷新回呼
 *   （onDraftChanged/onAdopted）。
 */
export function usePromptSandboxDrafts(deps: PromptSandboxDraftsDeps) {
  const { activeRun } = deps;

  const pickerRef = ref<PromptVersionPickerHandle>();
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

  return {
    pickerRef,
    draftEditor,
    adopt,
    openDraftEditor,
    onDraftChanged,
    onAdopted,
    openAdopt,
  };
}

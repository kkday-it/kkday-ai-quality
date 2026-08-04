// AI 定點改寫的一次「跑 → 勾 → 套 → 存」流程狀態。抽成 composable 的理由同 usePromptReviewCases：
// 流程有四段、每段各自有 loading/錯誤，全塞進元件會讓 template 綁一堆散狀 ref，也不好測。
import { computed, ref, type Ref } from 'vue';
import { Message } from '@arco-design/web-vue';
import {
  applyPromptPatches,
  savePromptDraft,
  streamPromptRevise,
  type PromptDebugUsage,
  type PromptPatch,
  type PromptReviseMeta,
  type PromptReviseResult,
  type PromptVersionSaved,
} from '@/api';
import type { LlmOverrides } from '@/features/settings/types';
import { usePromptReviewCasesStore } from '@/stores/promptReviewCases.store';

/** usePromptRevise 的注入依賴。 */
interface PromptReviseDeps {
  /** 要被改寫的現行 Prompt 全文（頁面上編輯中的那份）。 */
  systemPrompt: Ref<string>;
  /** 要餵給模型的案例 id（來自案例庫勾選）。 */
  reviewIds: Ref<number[]>;
}

/**
 * AI 定點改寫流程。
 * @returns 串流三態（`streaming`/`rawOutput`/`errorMessage`）、結果（`meta`/`result`/`usage`）、
 *   補丁勾選（`selected`/`toggle`/`selectedPatches`）、動作（`run`/`abort`/`apply`/`saveVersion`/`reset`）、
 *   以及套用後的 `revisedPrompt`（非空＝可進 diff 預覽與存版）。
 */
export function usePromptRevise(deps: PromptReviseDeps) {
  const streaming = ref(false);
  const rawOutput = ref('');
  const meta = ref<PromptReviseMeta | null>(null);
  const result = ref<PromptReviseResult | null>(null);
  const usage = ref<PromptDebugUsage | null>(null);
  /** 相容端點參數降級提示（每次串流重置；與調試台同一套文案）。 */
  const warnings = ref<string[]>([]);
  const errorMessage = ref('');
  /** 勾選的補丁索引；只有 status=matched 的能進來。 */
  const selected = ref<number[]>([]);
  /** 套用後的新全文；空＝還沒套用。 */
  const revisedPrompt = ref('');
  const applying = ref(false);
  const savingVersion = ref(false);
  let abortController: AbortController | null = null;

  const patches = computed<PromptPatch[]>(() => result.value?.patches ?? []);
  const selectedPatches = computed(() =>
    selected.value.map((i) => patches.value[i]).filter(Boolean),
  );
  /** 對不上／撞多處的補丁數（顯示成提醒，讓人知道模型還想改哪些但套不了）。 */
  const unusableCount = computed(() => patches.value.filter((p) => p.status !== 'matched').length);
  const canApply = computed(() => !applying.value && selectedPatches.value.length > 0);

  function reset(): void {
    rawOutput.value = '';
    meta.value = null;
    result.value = null;
    usage.value = null;
    warnings.value = [];
    errorMessage.value = '';
    selected.value = [];
    revisedPrompt.value = '';
  }

  /** 勾／取消勾某條補丁（不可套用的一律擋掉）。 */
  function toggle(index: number): void {
    if (patches.value[index]?.status !== 'matched') return;
    const at = selected.value.indexOf(index);
    if (at >= 0) selected.value.splice(at, 1);
    else selected.value.push(index);
  }

  async function run(overrides: LlmOverrides): Promise<void> {
    if (streaming.value || !deps.reviewIds.value.length) return;
    reset();
    streaming.value = true;
    abortController = new AbortController();
    try {
      await streamPromptRevise(
        {
          cases: usePromptReviewCasesStore().payloads(deps.reviewIds.value),
          system_prompt: deps.systemPrompt.value,
          overrides,
        },
        {
          onMeta: (value) => (meta.value = value),
          onDelta: (text) => (rawOutput.value += text),
          onWarning: (message) => warnings.value.push(message),
          onResult: (value) => {
            result.value = value;
            // 預設勾選全部可套用的：多數情況人是全收，逐條取消比逐條勾快
            selected.value = value.patches
              .map((p, i) => (p.status === 'matched' ? i : -1))
              .filter((i) => i >= 0);
          },
          onUsage: (value) => (usage.value = value),
          onError: (message) => (errorMessage.value = message),
        },
        abortController.signal,
      );
    } catch (error) {
      if ((error as Error).name !== 'AbortError') {
        errorMessage.value = error instanceof Error ? error.message : String(error);
      }
    } finally {
      streaming.value = false;
      abortController = null;
    }
  }

  function abort(): void {
    abortController?.abort();
  }

  /** 套用勾選補丁（後端做 anchor 驗證與由後往前替換），成功後填 `revisedPrompt` 供 diff 預覽。 */
  async function apply(): Promise<void> {
    if (!canApply.value) return;
    applying.value = true;
    try {
      const res = await applyPromptPatches(
        deps.systemPrompt.value,
        selectedPatches.value.map((p) => ({ anchor: p.anchor, replacement: p.replacement })),
      );
      revisedPrompt.value = res.system_prompt;
      const delta = res.chars_after - res.chars_before;
      Message.success(
        `已套用 ${selectedPatches.value.length} 條補丁（${delta >= 0 ? '+' : ''}${delta} 字元），請在下方比對後再存版`,
      );
    } catch (error) {
      Message.error(error instanceof Error ? error.message : '套用補丁失敗');
    } finally {
      applying.value = false;
    }
  }

  /**
   * 把套用後的全文存成新草稿（**不改變線上口徑**）。
   *
   * @returns 存檔結果（`version`＝草稿名、`created`＝是否真的建了新檔）；失敗回 `null`。
   *
   * ⚠️ **必須回傳 `version`**：流水線步驟④的「升為正式版」要拿它當 `promotePromptRelease` 的
   * 來源草稿名。這裡曾經只回 `boolean`，版本號僅存在於 toast 字串裡，等於後續路徑接不上。
   *
   * ⚠️ **`created === false` 不是失敗**：內容與最新草稿逐字相同時後端不建檔，但**那支既有草稿
   * 仍然可以升版**。呼叫端不得把它當錯誤而中斷流程（舊版就是這樣把「內容沒變」的路徑整條斷掉的）。
   */
  async function saveVersion(): Promise<PromptVersionSaved | null> {
    if (!revisedPrompt.value.trim() || savingVersion.value) return null;
    savingVersion.value = true;
    try {
      const saved = await savePromptDraft(revisedPrompt.value, 'AI 定點改寫套用補丁');
      Message.success(
        saved.created
          ? `已存為新草稿 ${saved.version}`
          : `內容與最新草稿 ${saved.version} 相同，直接沿用該草稿`,
      );
      return saved;
    } catch (error) {
      Message.error(error instanceof Error ? error.message : '存為新草稿失敗');
      return null;
    } finally {
      savingVersion.value = false;
    }
  }

  return {
    streaming,
    rawOutput,
    meta,
    result,
    usage,
    warnings,
    errorMessage,
    patches,
    selected,
    selectedPatches,
    unusableCount,
    canApply,
    revisedPrompt,
    applying,
    savingVersion,
    run,
    abort,
    apply,
    saveVersion,
    reset,
    toggle,
  };
}

// 案例庫 × AI 改寫流水線的閘門與失效判定（純函式）。
//
// 抽成 util 而非留在抽屜裡的理由有二：① 這是整條流水線的正確性核心——「哪一步可進入」與
// 「回歸結果什麼時候該作廢」錯了，使用者就能拿舊的綠燈發布新的內容，值得被單測鎖住；
// ② 專案的元件測試要逐檔標 `@vitest-environment jsdom`（全域 env 是 node），純函式測試
// 又快又穩，且符合「元件薄、邏輯下沉」。

/** 流水線步驟：① 選案例 → ② AI 改寫 → ③ 回歸驗證 → ④ 定案發布。 */
export type PipelineStep = 1 | 2 | 3 | 4;

/** 判定閘門所需的最小狀態切片（刻意只吃值、不吃 ref，維持純函式可測）。 */
export interface PipelineState {
  /** ① 勾選的案例 id。 */
  selectedIds: number[];
  /** ② 套用補丁後的候選 Prompt 全文；空＝還沒產生候選版。 */
  candidatePrompt: string;
  /** ③ 回歸是否已**跑完**（執行中不算——沒有結論就不能進定案）。 */
  regressionDone: boolean;
}

/**
 * 某一步被擋住的原因。
 *
 * @param step 目標步驟。
 * @param state 當前流水線狀態。
 * @returns 空字串＝可進入；非空＝擋住的原因（直接當 tooltip 文案用）。
 */
export function stepBlockedReason(step: PipelineStep, state: PipelineState): string {
  if (step >= 2 && !state.selectedIds.length) return '請先在①勾選要修的案例';
  if (step >= 3 && !state.candidatePrompt) return '請先在②套用補丁產生候選版';
  if (step >= 4 && !state.regressionDone) return '請先在③跑一次回歸驗證';
  return '';
}

/** 某一步能不能進入（`stepBlockedReason` 的布林封裝，模板用得順）。 */
export function canEnterStep(step: PipelineStep, state: PipelineState): boolean {
  return !stepBlockedReason(step, state);
}

/**
 * 當前狀態下最高可達的步驟。
 *
 * 用途是 route query 還原時的 clamp：`?revise=3` 在重整後必然不成立（案例勾選與候選 Prompt
 * 都沒有持久化），直接照著 query 跳過去會落在一個閘門不通過的步驟。回到最高可達的那一步是
 * 誠實行為——候選版本本來就沒存下來。
 */
export function highestReachableStep(state: PipelineState): PipelineStep {
  if (canEnterStep(4, state)) return 4;
  if (canEnterStep(3, state)) return 3;
  if (canEnterStep(2, state)) return 2;
  return 1;
}

/** 把任意輸入夾到「合法且當前可達」的步驟（query 還原、外部跳轉共用）。 */
export function clampStep(step: number, state: PipelineState): PipelineStep {
  const wanted = Math.min(4, Math.max(1, Math.trunc(step) || 1)) as PipelineStep;
  const ceiling = highestReachableStep(state);
  return (wanted <= ceiling ? wanted : ceiling) as PipelineStep;
}

/**
 * 兩組案例 id 是不是同一個集合（不計順序與重複）。
 *
 * 「回歸失效」刻意用集合比對而不是 `watch` 陣列：回到①**只是回頭看一眼勾了哪些**是很常見的
 * 動作，不該把辛苦跑出來的回歸結果清掉；真的增刪了案例才失效。
 */
export function sameIdSet(a: readonly number[], b: readonly number[]): boolean {
  const left = new Set(a);
  const right = new Set(b);
  if (left.size !== right.size) return false;
  for (const id of left) if (!right.has(id)) return false;
  return true;
}

/** 回歸結果失效的兩個入口（回傳 true＝該把回歸結果作廢）。 */
export interface RegressionValidity {
  /** 回歸跑當下的候選 Prompt 全文。 */
  validatedPrompt: string;
  /** 回歸跑當下的案例 id 集合。 */
  validatedIds: readonly number[];
}

/**
 * 回歸結果是否已對不上當前狀態。
 *
 * 沒有這道判斷，使用者可以「套補丁 A → 回歸綠燈 → 改成補丁 B → 直接發布」，等於**拿 A 的綠燈
 * 發布了 B**。案例集合同理：回歸是針對「特定案例集合 + 特定 Prompt」跑的，只換案例不換 Prompt
 * 一樣能拿舊結果闖關。
 *
 * @param snapshotAt 回歸執行當下記錄的基準；`null`＝根本還沒跑過，無所謂失效。
 * @param current 當前的候選 Prompt 與案例勾選。
 */
export function isRegressionStale(
  snapshotAt: RegressionValidity | null,
  current: { candidatePrompt: string; selectedIds: readonly number[] },
): boolean {
  if (!snapshotAt) return false;
  if (snapshotAt.validatedPrompt !== current.candidatePrompt) return true;
  return !sameIdSet(snapshotAt.validatedIds, current.selectedIds);
}

/**
 * 「升為正式版」被擋住的原因。
 *
 * 與「存為新草稿」的門檻刻意不同：草稿不影響線上口徑，改壞了也該讓人留存半成品；
 * 把改壞的版本推上線才是要擋的那件事。
 *
 * @param brokenCount 回歸判定「原本判對卻被改壞」的欄數。
 * @param hasDraft 是否已存出草稿（升版的來源必須是已存檔的草稿）。
 */
export function publishBlockedReason(brokenCount: number, hasDraft: boolean): string {
  if (!hasDraft) return '請先「存為新草稿」——升版的來源必須是已存檔的草稿';
  if (brokenCount > 0) return `回歸有 ${brokenCount} 個欄位被改壞，這版不該上線`;
  return '';
}

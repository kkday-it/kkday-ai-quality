// 回歸重跑的啟動 + 進度輪詢。走輪詢而非 SSE：後端是 in-mem job，
// 前端只要每兩秒問一次進度即可，不需要為此另拉一條串流。
import { computed, onBeforeUnmount, ref } from 'vue';
import { Message } from '@arco-design/web-vue';
import { getPromptRegression, startPromptRegression, type PromptRegressionSnapshot } from '@/api';
import type { LlmOverrides } from '@/features/settings/types';
import { usePromptReviewCasesStore } from '@/stores/promptReviewCases.store';

/** 進度輪詢間隔：單條重跑約數秒到數十秒，兩秒一次足夠即時又不會打爆後端。 */
const POLL_MS = 2000;

/**
 * 回歸重跑流程。
 * @returns `snapshot` 進度快照、`running` 是否進行中、`summary` 彙總數字、
 *   `brokenCases` 被改壞的案例、以及 `start`/`reset` 動作。
 */
export function usePromptRegression() {
  const snapshot = ref<PromptRegressionSnapshot | null>(null);
  const starting = ref(false);
  const errorMessage = ref('');
  let timer: ReturnType<typeof setTimeout> | null = null;

  const running = computed(() => starting.value || snapshot.value?.status === 'running');
  /** 有任何欄位被改壞＝這次改寫不該直接上線。 */
  const hasRegression = computed(() => (snapshot.value?.broken ?? 0) > 0);
  const brokenCases = computed(
    () => snapshot.value?.cases.filter((c) => c.broken.length > 0) ?? [],
  );
  const unfixedCases = computed(
    () => snapshot.value?.cases.filter((c) => c.still_wrong.length > 0) ?? [],
  );

  function stopPolling(): void {
    if (timer) clearTimeout(timer);
    timer = null;
  }

  function reset(): void {
    stopPolling();
    snapshot.value = null;
    errorMessage.value = '';
  }

  async function poll(jobId: string): Promise<void> {
    try {
      const snap = await getPromptRegression(jobId);
      snapshot.value = snap;
      if (snap.status === 'running') {
        timer = setTimeout(() => void poll(jobId), POLL_MS);
        return;
      }
      stopPolling();
      if (snap.status === 'error') errorMessage.value = snap.error || '回歸執行失敗';
    } catch (error) {
      stopPolling();
      // 後端重啟會讓 in-mem job 消失（404）——講清楚是什麼情況，別讓人以為資料壞了
      errorMessage.value = error instanceof Error ? error.message : '回歸進度查詢失敗';
    }
  }

  /**
   * 啟動回歸。
   * @param reviewIds 要重跑的案例 id
   * @param systemPrompt 候選 Prompt 全文（可以是還沒存版的草稿）
   * @param overrides 本次 LLM 覆寫
   */
  async function start(
    reviewIds: number[],
    systemPrompt: string,
    overrides: LlmOverrides,
  ): Promise<void> {
    if (running.value || !reviewIds.length) return;
    reset();
    starting.value = true;
    try {
      const snap = await startPromptRegression({
        cases: usePromptReviewCasesStore().payloads(reviewIds),
        system_prompt: systemPrompt,
        overrides,
      });
      snapshot.value = snap;
      timer = setTimeout(() => void poll(snap.job_id), POLL_MS);
    } catch (error) {
      errorMessage.value = error instanceof Error ? error.message : '啟動回歸失敗';
      Message.error(errorMessage.value);
    } finally {
      starting.value = false;
    }
  }

  onBeforeUnmount(stopPolling);

  return {
    snapshot,
    running,
    errorMessage,
    hasRegression,
    brokenCases,
    unfixedCases,
    start,
    reset,
  };
}

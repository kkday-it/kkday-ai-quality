// 人工評判案例庫的載入 / 刪除 / 勾選狀態。抽成 composable 的理由：案例清單同時被三個消費點吃
// （列表分頁、AI 改寫的證據來源、回歸重跑的目標集），勾選狀態必須是同一份，散在元件裡會各拿各的。
import { computed, ref } from 'vue';
import { Message } from '@arco-design/web-vue';
import { deletePromptDebugReview, listPromptDebugReviews, type PromptDebugReviewRow } from '@/api';

/**
 * 案例庫狀態機（載入三態 + 勾選）。
 * @returns `cases`/`loading`/`error` 三態、`selectedIds` 勾選、`selectedCases` 勾選明細，
 *   以及 `load`/`remove` 動作。
 */
export function usePromptReviewCases() {
  const cases = ref<PromptDebugReviewRow[]>([]);
  const loading = ref(false);
  const error = ref('');
  const selectedIds = ref<number[]>([]);

  const selectedCases = computed(() =>
    cases.value.filter((row) => selectedIds.value.includes(row.id)),
  );
  /** 有標錯欄位的案例才是「誤判證據」；全對的是回歸正例，AI 改寫時價值不同。 */
  const badCount = (row: PromptDebugReviewRow): number => Object.keys(row.corrections ?? {}).length;

  async function load(): Promise<void> {
    loading.value = true;
    error.value = '';
    try {
      const { reviews } = await listPromptDebugReviews();
      cases.value = reviews;
      // 清掉已不存在的勾選（他人刪過、或本地刪除後重載）
      const alive = new Set(reviews.map((r) => r.id));
      selectedIds.value = selectedIds.value.filter((id) => alive.has(id));
    } catch (e) {
      error.value = e instanceof Error ? e.message : '載入案例庫失敗';
    } finally {
      loading.value = false;
    }
  }

  async function remove(id: number): Promise<void> {
    try {
      await deletePromptDebugReview(id);
      cases.value = cases.value.filter((row) => row.id !== id);
      selectedIds.value = selectedIds.value.filter((x) => x !== id);
      Message.success('已刪除案例');
    } catch (e) {
      Message.error(e instanceof Error ? e.message : '刪除失敗');
    }
  }

  return { cases, loading, error, selectedIds, selectedCases, badCount, load, remove };
}

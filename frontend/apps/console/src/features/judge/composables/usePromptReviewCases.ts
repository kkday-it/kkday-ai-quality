// 人工評判案例庫的讀取 / 刪除 / 勾選狀態。抽成 composable 的理由：案例清單同時被三個消費點吃
// （列表分頁、AI 改寫的證據來源、回歸重跑的目標集），勾選狀態必須是同一份，散在元件裡會各拿各的。
//
// 2026-08-04 起資料源改為**瀏覽器本地** store（見 stores/promptReviewCases.store）——案例是個人
// 調試用的暫存語料，不落 DB。故 `load` 不再發請求；三態中的 loading/error 保留但恆為閒置值，
// 讓消費端的三態渲染零改動（日後若要換回遠端，介面不必再改一次）。
import { computed, ref } from 'vue';
import { Message } from '@arco-design/web-vue';
import { usePromptReviewCasesStore, type PromptReviewCase } from '@/stores/promptReviewCases.store';

/**
 * 案例庫狀態機（三態 + 勾選）。
 * @returns `cases`/`loading`/`error` 三態、`selectedIds` 勾選、`selectedCases` 勾選明細，
 *   以及 `load`/`remove` 動作。
 */
export function usePromptReviewCases() {
  const store = usePromptReviewCasesStore();
  const loading = ref(false);
  const error = ref('');
  const selectedIds = ref<number[]>([]);

  const cases = computed<PromptReviewCase[]>(() => store.sorted);

  const selectedCases = computed(() =>
    cases.value.filter((row) => selectedIds.value.includes(row.id)),
  );
  /** 有標錯欄位的案例才是「誤判證據」；全對的是回歸正例，AI 改寫時價值不同。 */
  const badCount = (row: PromptReviewCase): number => Object.keys(row.corrections ?? {}).length;

  /** 對齊已不存在的勾選（本地刪除後）。資料本身即時響應，不需重新取數。 */
  function load(): void {
    const alive = new Set(cases.value.map((r) => r.id));
    selectedIds.value = selectedIds.value.filter((id) => alive.has(id));
  }

  function remove(id: number): void {
    if (store.remove(id)) {
      selectedIds.value = selectedIds.value.filter((x) => x !== id);
      Message.success('已刪除案例');
    } else {
      Message.error('案例不存在');
    }
  }

  return { cases, loading, error, selectedIds, selectedCases, badCount, load, remove };
}

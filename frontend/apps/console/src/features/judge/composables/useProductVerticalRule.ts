// 商品垂直分類（product_vertical）獨立編輯態：抽屜專用。
// **刻意不共用 useJudgeRulesStore**——那是 singleton，其 activeCode 被規則配置頁同時消費，
// 抽屜若透過它 selectRule('product_vertical') 會改共用 activeCode，令規則頁背景誤渲染本規則。
// 本 composable 持有自己的 local state，直接呼叫 rule 版本化 API（固定 code=product_vertical），
// 與規則頁完全解耦。
import { computed, ref } from 'vue';
import {
  getRule,
  getRuleHistory,
  resetRuleDefault,
  saveRule,
} from '@/api/judgeRules.api';

const CODE = 'product_vertical';

/**
 * 商品垂直分類單規則編輯（載入 / 編輯 / 存檔 / 恢復默認 + active 版本 meta）。
 * @returns 隔離的編輯態與操作；不觸碰任何全域 store 的 activeCode。
 */
export function useProductVerticalRule() {
  const baseline = ref<Record<string, unknown> | null>(null); // 載入時 content（dirty 基準）
  const edited = ref<Record<string, unknown> | null>(null); // 編輯中 content
  const editValid = ref(true);
  const version = ref<number | null>(null); // 當前 active 版本號（header 顯示）
  const createdAt = ref<string | null>(null); // 當前 active 版建立時間
  const loading = ref(false);
  const error = ref('');

  const dirty = computed(
    () =>
      editValid.value &&
      edited.value != null &&
      JSON.stringify(edited.value) !== JSON.stringify(baseline.value),
  );

  /** 載入 active content + active 版本 meta（供 header 版本號顯示）。 */
  async function load() {
    loading.value = true;
    error.value = '';
    try {
      const [content, history] = await Promise.all([getRule(CODE), getRuleHistory(CODE)]);
      baseline.value = content.content;
      edited.value = content.content;
      editValid.value = true;
      const active = history.find((h) => h.is_active);
      version.value = active?.version ?? null;
      createdAt.value = active?.created_at ?? null;
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e);
    } finally {
      loading.value = false;
    }
  }

  /** 分組表單回報變更（合法才更新編輯態）。 */
  function setEdited(content: unknown, valid: boolean) {
    editValid.value = valid;
    if (valid) edited.value = content as Record<string, unknown>;
  }

  /** 存檔（後端驗證 + 新 active 版）；成功後重載對齊版本號。 */
  async function save(note: string) {
    if (!dirty.value || !edited.value) return;
    await saveRule(CODE, edited.value, note);
    await load();
  }

  /** 恢復檔案默認（新增一版覆蓋當前）。 */
  async function resetDefault() {
    await resetRuleDefault(CODE);
    await load();
  }

  return {
    code: CODE,
    edited,
    version,
    createdAt,
    loading,
    error,
    dirty,
    load,
    setEdited,
    save,
    resetDefault,
  };
}

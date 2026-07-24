// 初判規則狀態（product_vertical/source_mapping + prompt_* 的 active content / dirty / 歷史 / 存檔恢復）。
// 檔案＝默認 seed，DB＝live+歷史；所有寫操作走後端版本化，store 只持有當前選中 rule 的編輯態。
import { defineStore } from 'pinia';
import { computed, ref } from 'vue';
import {
  getRule,
  getRuleHistory,
  listRules,
  resetAllRuleDefaults,
  resetRuleDefault,
  restoreRule,
  saveRule,
  type RuleCode,
  type RuleMeta,
  type RuleVersionMeta,
} from '@/api/judgeRules.api';

/** 顯示名 fallback（各 rule 中文名一律優先後端 meta.label，content._meta.label，SSOT，見 store.labelFor
 * ——不再於前端各寫一份而漂移；此處僅補既有 DB 若在補 _meta 前即 seed 過導致 label 為 null 的情況）。 */
export const RULE_LABELS_FALLBACK: Record<string, string> = {
  // 商品垂直分類：seed 檔已含 _meta.label，但既有 DB 若在補 _meta 前即 seed 過（label 為 None）時，
  // 由此 fallback 補顯示名，避免選單顯示原始 code。
  product_vertical: '商品垂直分類',
  // 上傳表頭校驗：seed 檔含 _meta.label，DB 未 seed 時由此 fallback。
  source_mapping: '上傳表頭校驗',
};

export const useJudgeRulesStore = defineStore('judgeRules', () => {
  const metas = ref<RuleMeta[]>([]); // 各 rule active 版 meta
  const activeCode = ref<RuleCode>('source_mapping'); // 當前選中子規則
  const baseline = ref<Record<string, unknown> | null>(null); // 載入時的 content（dirty 比對基準）
  const edited = ref<Record<string, unknown> | null>(null); // 編輯中 content（合法時更新）
  const editValid = ref(true); // JSON 模式語法/結構合法
  const history = ref<RuleVersionMeta[]>([]);
  const loading = ref(false);
  const error = ref('');

  const dirty = computed(
    () =>
      editValid.value &&
      edited.value != null &&
      JSON.stringify(edited.value) !== JSON.stringify(baseline.value),
  );
  const currentMeta = computed(() => metas.value.find((m) => m.rule_code === activeCode.value));

  /** rule code → 顯示名：優先後端 meta.label（SSOT），非域偽 rule 回退 fallback，最後回 code 本身。 */
  function labelFor(code: string): string {
    const m = metas.value.find((x) => x.rule_code === code);
    return m?.label || RULE_LABELS_FALLBACK[code] || code;
  }

  /** 載入清單（各 rule active meta）。 */
  async function loadList() {
    metas.value = await listRules();
  }

  /** 切換並載入某 rule 的 active content。 */
  async function selectRule(code: RuleCode) {
    activeCode.value = code;
    loading.value = true;
    error.value = '';
    try {
      const r = await getRule(code);
      baseline.value = r.content;
      edited.value = r.content;
      editValid.value = true;
      history.value = [];
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e);
    } finally {
      loading.value = false;
    }
  }

  /** JSON / 面板編輯回報的內容變更。 */
  function setEdited(content: unknown, valid: boolean) {
    editValid.value = valid;
    if (valid) edited.value = content as Record<string, unknown>;
  }

  /** 存檔（後端驗證 + 新版）。成功後重載 list + 當前 rule。 */
  async function save(note: string) {
    if (!dirty.value || !edited.value) return;
    await saveRule(activeCode.value, edited.value, note);
    await Promise.all([loadList(), selectRule(activeCode.value)]);
  }

  /** 載入當前 rule 歷史。 */
  async function loadHistory() {
    history.value = await getRuleHistory(activeCode.value);
  }

  /** 恢復某歷史版本。 */
  async function restore(version: number) {
    await restoreRule(activeCode.value, version);
    await Promise.all([loadList(), selectRule(activeCode.value), loadHistory()]);
  }

  /** 恢復默認（檔案 seed）。 */
  async function resetDefault() {
    await resetRuleDefault(activeCode.value);
    await Promise.all([loadList(), selectRule(activeCode.value)]);
  }

  /** 恢復全部規則（source_mapping + 7 支初判 Prompt）為檔案默認，各新增版本；重載清單與當前選中，回傳 {reset, skipped}。 */
  async function resetAllDefault() {
    const res = await resetAllRuleDefaults();
    await Promise.all([loadList(), selectRule(activeCode.value)]);
    return res;
  }

  return {
    metas,
    activeCode,
    baseline,
    edited,
    editValid,
    history,
    loading,
    error,
    dirty,
    currentMeta,
    labelFor,
    loadList,
    selectRule,
    setEdited,
    save,
    loadHistory,
    restore,
    resetDefault,
    resetAllDefault,
  };
});

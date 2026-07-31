// 售後根因 Prompt 的「升版／回退」動作（草稿 → 正式版、切換 active 指標）。
//
// 從 `PromptVersionDrawer` 提取的理由不是「將來可能有第二個消費端」，而是**現在已經有三處**：
// 版本列表抽屜（升版本體）、調試台頁面（`promotedDrafts` 判斷哪支草稿還沒升過，原本是複製的
// 第二份）、以及流水線步驟④的就地升版。名稱建議規則、撞名檢查、必填理由這些判準散成三份必 drift。
import { computed, toValue, type MaybeRefOrGetter } from 'vue';
import { ref } from 'vue';
import { Message, Modal } from '@arco-design/web-vue';
import { activatePromptRelease, PERM, promotePromptRelease, type PromptReleaseMeta } from '@/api';
import { usePermission } from '@/composables/usePermission';

/** `usePromptRelease` 的注入依賴。 */
interface PromptReleaseDeps {
  /** 正式版清單——名稱建議與撞名檢查都靠它。各消費端資料源不同，故以參數注入而非自行取得。 */
  releases: MaybeRefOrGetter<PromptReleaseMeta[]>;
  /**
   * 升版或回退成功後的回呼（帶新的 active 正式版名）。
   *
   * 刻意用 callback 而不是讓 composable 自己重載資料：線上口徑變了之後要刷新什麼，是消費端
   * 才知道的事（抽屜只需 emit 給父層、頁面則要重載 defaults）。
   */
  onDone?: (name: string) => void;
}

/**
 * 升版／回退動作組。
 * @returns 權限旗標、已升版草稿集合、升版表單狀態與校驗、以及 `openPromote` / `confirmPromote` /
 *   `activate` 三個動作。升版確認框的**版面**留在各消費端（不同載體版面不同），這裡只管狀態與行為。
 */
export function usePromptRelease(deps: PromptReleaseDeps) {
  const { can } = usePermission();
  const canManage = computed(() => can(PERM.judgeRuleManage));

  /** 已被升版過的草稿名集合（`releases[].source_draft`）——這些草稿不需要、也不該再升一次。 */
  const promotedDrafts = computed(
    () => new Set(toValue(deps.releases).map((r) => r.source_draft).filter(Boolean)),
  );

  // ── 升版（草稿 → 新正式版）──────────────────────────────────────────────────
  const promoteVisible = ref(false);
  const promoting = ref(false);
  const sourceDraft = ref('');
  const releaseName = ref('');
  const releaseNote = ref('');

  /** 下一個建議名稱：release-v{既有最大序號+1}；解析不出序號就退回「總數+1」。 */
  const suggestedName = computed(() => {
    const releases = toValue(deps.releases);
    const nums = releases
      .map((r) => /^release-v(\d+)$/.exec(r.name)?.[1])
      .filter((x): x is string => !!x)
      .map(Number);
    return `release-v${(nums.length ? Math.max(...nums) : releases.length) + 1}`;
  });

  const nameValid = computed(() =>
    /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(releaseName.value.trim()),
  );
  const nameTaken = computed(() =>
    toValue(deps.releases).some((r) => r.name === releaseName.value.trim()),
  );
  const noteValid = computed(() => !!releaseNote.value.trim());
  const canConfirmPromote = computed(
    () => nameValid.value && !nameTaken.value && noteValid.value && !promoting.value,
  );

  /** 開啟升版表單並帶入建議名稱。 */
  function openPromote(draft: string): void {
    sourceDraft.value = draft;
    releaseName.value = suggestedName.value;
    releaseNote.value = '';
    promoteVisible.value = true;
  }

  /** 送出升版；成功後關閉表單並呼叫 `onDone`。 */
  async function confirmPromote(): Promise<void> {
    if (!canConfirmPromote.value) return;
    promoting.value = true;
    try {
      const out = await promotePromptRelease(
        sourceDraft.value,
        releaseName.value.trim(),
        releaseNote.value.trim(),
      );
      Message.success(`${out.name} 已成為線上口徑（前一版 ${out.previous_active || '—'}）`);
      promoteVisible.value = false;
      deps.onDone?.(out.name);
    } catch (error) {
      Message.error(error instanceof Error ? error.message : '升版失敗');
    } finally {
      promoting.value = false;
    }
  }

  // ── 回退（把 active 指標切到某個既有正式版）────────────────────────────────
  /** 正在切換的正式版名（供 per-row `:loading`）；空＝閒置。 */
  const activating = ref('');

  /**
   * 把線上口徑切到某個既有正式版。閉環的最後一塊——沒有這條路，升錯版只能再升一版。
   * @param name 目標正式版名。
   */
  function activate(name: string): void {
    Modal.confirm({
      title: '切換線上口徑',
      content: `確認後 ${name} 立即成為線上唯一口徑，跑批與調試台的「正式」側都會改用它。`,
      okText: '設為使用中',
      cancelText: '取消',
      onOk: async () => {
        activating.value = name;
        try {
          const out = await activatePromptRelease(name);
          Message.success(`線上口徑已切為 ${out.name}（前一版 ${out.previous_active || '—'}）`);
          deps.onDone?.(out.name);
        } catch (error) {
          Message.error(error instanceof Error ? error.message : '切換失敗');
        } finally {
          activating.value = '';
        }
      },
    });
  }

  return {
    canManage,
    promotedDrafts,
    promoteVisible,
    promoting,
    sourceDraft,
    releaseName,
    releaseNote,
    suggestedName,
    nameValid,
    nameTaken,
    noteValid,
    canConfirmPromote,
    openPromote,
    confirmPromote,
    activating,
    activate,
  };
}

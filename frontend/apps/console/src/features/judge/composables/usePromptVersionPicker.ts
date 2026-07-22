// 7 條初判 prompt 的版本選擇（初判分類指定歷史版本 + Prompt 測試沙盒共用）：每支 prompt 一個
// 下拉，選項＝該 rule_code 全版本歷史（getRuleHistory）。沙盒另支援草稿模式（withDrafts）：
// 有 DB 草稿的 prompt 多一個「草稿」選項，選中即以草稿內容送測（雙跑對比）；正式初判不帶草稿。
import { computed, ref } from 'vue';
import {
  getRuleHistory,
  listRuleDrafts,
  type PromptDraftMeta,
  type RuleVersionMeta,
} from '@/api/judgeRules.api';
import { useJudgeRulesStore } from '@/stores/judgeRules.store';
import { versionLabel } from '../utils/ruleVersion.util';

export interface PromptVersionOption {
  value: number;
  label: string;
}

/** 下拉的「草稿」選項哨兵值（真實版本號恆 ≥1，-1 不會撞號）。 */
export const DRAFT_VERSION = -1;

/** 解析後可直接展開進請求 body：僅含「非 active」的指定版本。 */
export interface ResolvedPromptSelection {
  versions: Record<string, number>;
}

export function usePromptVersionPicker(opts: {
  /** true 時每支 prompt 多一個「要不要納入本次測試」開關（Prompt 測試沙盒用；正式初判固定
   * 全 7 支恆納入，不需要這個開關，維持 false）。 */
  withToggle?: boolean;
  /** true 時載入草稿存在狀態：有草稿的 prompt 下拉多一個「草稿」選項（選中＝以草稿送測）。
   * 僅沙盒用；正式初判不帶草稿，維持 false。 */
  withDrafts?: boolean;
}) {
  const store = useJudgeRulesStore();
  const historyByCode = ref<Record<string, RuleVersionMeta[]>>({});
  const selected = ref<Record<string, number>>({});
  /** withToggle 時各 prompt 是否納入本次測試；預設僅 polarity 開（極簡預設，免每次手動全勾）。 */
  const enabled = ref<Record<string, boolean>>({});
  /** withDrafts 時各 prompt 的草稿存在狀態（rule_code → meta；無草稿＝無鍵）。 */
  const draftMetas = ref<Record<string, PromptDraftMeta>>({});

  const promptCodes = computed(() =>
    store.metas
      .filter((m) => m.rule_code.startsWith('prompt_'))
      .map((m) => m.rule_code)
      .sort((a, b) =>
        a === 'prompt_polarity' ? -1 : b === 'prompt_polarity' ? 1 : a.localeCompare(b),
      ),
  );

  function activeVersionOf(code: string): number | undefined {
    return store.metas.find((m) => m.rule_code === code)?.version;
  }

  /** 重新拉草稿存在狀態（草稿編輯抽屜存檔/刪除後呼叫）；草稿消失但仍選中者退回 active。 */
  async function refreshDrafts(): Promise<void> {
    if (!opts.withDrafts) return;
    const metas = await listRuleDrafts();
    draftMetas.value = Object.fromEntries(metas.map((m) => [m.rule_code, m]));
    for (const code of Object.keys(selected.value)) {
      if (selected.value[code] === DRAFT_VERSION && !draftMetas.value[code]) {
        selected.value[code] = activeVersionOf(code) ?? 0;
      }
    }
  }

  /** 載入 7 條 prompt 的 meta + 各自版本歷史（+ 草稿存在狀態），並設定預設選中值（active）。 */
  async function ensureLoaded(): Promise<void> {
    if (!store.metas.length) await store.loadList();
    await Promise.all([
      ...promptCodes.value.map(async (code) => {
        if (!historyByCode.value[code]) {
          historyByCode.value[code] = await getRuleHistory(code);
        }
      }),
      refreshDrafts(),
    ]);
    for (const code of promptCodes.value) {
      if (selected.value[code] == null) {
        selected.value[code] = activeVersionOf(code) ?? 0;
      }
      if (opts.withToggle && enabled.value[code] == null) {
        enabled.value[code] = code === 'prompt_polarity';
      }
    }
  }

  function optionsFor(code: string): PromptVersionOption[] {
    const out: PromptVersionOption[] = (historyByCode.value[code] || []).map((v) => ({
      value: v.version,
      label: versionLabel(v.created_at, v.version) + (v.is_active ? '（active）' : ''),
    }));
    const meta = draftMetas.value[code];
    if (meta) {
      // stale 提示：草稿分叉後 active 又前進 → 標示基準已過時（仍可測，入庫前自行斟酌）
      const active = activeVersionOf(code);
      const stale = active != null && meta.base_version < active ? '·active 已前進' : '';
      out.unshift({ value: DRAFT_VERSION, label: `📝 草稿（基於 v${meta.base_version}${stale}）` });
    }
    return out;
  }

  /** withToggle 時實際納入本次測試的 rule_code（開關開著的）；非 withToggle（正式初判）恆全 7 支。 */
  const enabledCodes = computed(() =>
    opts.withToggle ? promptCodes.value.filter((c) => enabled.value[c]) : promptCodes.value,
  );

  /** 納入測試且選中「草稿」的 rule_code（沙盒送測時逐條取 DB 草稿內容快照帶入 body.drafts）。 */
  const draftCodes = computed(() =>
    enabledCodes.value.filter((c) => selected.value[c] === DRAFT_VERSION && draftMetas.value[c]),
  );

  const resolved = computed<ResolvedPromptSelection>(() => {
    const versions: Record<string, number> = {};
    for (const code of enabledCodes.value) {
      const sel = selected.value[code];
      // 草稿哨兵不進 versions（後端草稿優先於版本；基準沿用 active）
      if (typeof sel === 'number' && sel !== DRAFT_VERSION && sel !== activeVersionOf(code)) {
        versions[code] = sel; // 等於 active 不必帶，維持請求精簡、沿用既有 cache 快路徑
      }
    }
    return { versions };
  });

  /** 重拉某 code 的版本歷史並把選中值對齊新 active（草稿入庫後呼叫：新版本要出現在下拉）。 */
  async function reloadHistory(code: string): Promise<void> {
    await store.loadList(); // 先刷新 metas 拿到新 active 版本號
    historyByCode.value[code] = await getRuleHistory(code);
    selected.value[code] = activeVersionOf(code) ?? 0;
  }

  return {
    promptCodes,
    selected,
    enabled,
    enabledCodes,
    draftMetas,
    draftCodes,
    optionsFor,
    resolved,
    ensureLoaded,
    refreshDrafts,
    reloadHistory,
    activeVersionOf,
  };
}

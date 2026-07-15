// 7 條判決 prompt 的版本選擇（初判分類指定歷史版本 + Prompt 測試沙盒共用）：每支 prompt 一個
// 下拉，選項＝該 rule_code 全版本歷史（getRuleHistory）。所有 Prompt 測試都在歸因列表以「選版本」
// 進行，不支援測試未存檔草稿。
import { computed, ref } from 'vue';
import { getRuleHistory, type RuleVersionMeta } from '@/api/judgeRules.api';
import { useJudgeRulesStore } from '@/stores/judgeRules.store';
import { versionLabel } from '../utils/ruleVersion.util';

export interface PromptVersionOption {
  value: number;
  label: string;
}

/** 解析後可直接展開進請求 body：僅含「非 active」的指定版本。 */
export interface ResolvedPromptSelection {
  versions: Record<string, number>;
}

export function usePromptVersionPicker(opts: {
  /** true 時每支 prompt 多一個「要不要納入本次測試」開關（Prompt 測試沙盒用；正式判決固定
   * 全 7 支恆納入，不需要這個開關，維持 false）。 */
  withToggle?: boolean;
}) {
  const store = useJudgeRulesStore();
  const historyByCode = ref<Record<string, RuleVersionMeta[]>>({});
  const selected = ref<Record<string, number>>({});
  /** withToggle 時各 prompt 是否納入本次測試；預設僅 polarity 開（極簡預設，免每次手動全勾）。 */
  const enabled = ref<Record<string, boolean>>({});

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

  /** 載入 7 條 prompt 的 meta + 各自版本歷史，並設定預設選中值（active）。 */
  async function ensureLoaded(): Promise<void> {
    if (!store.metas.length) await store.loadList();
    await Promise.all(
      promptCodes.value.map(async (code) => {
        if (!historyByCode.value[code]) {
          historyByCode.value[code] = await getRuleHistory(code);
        }
      }),
    );
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
    return (historyByCode.value[code] || []).map((v) => ({
      value: v.version,
      label: versionLabel(v.created_at, v.version) + (v.is_active ? '（active）' : ''),
    }));
  }

  /** withToggle 時實際納入本次測試的 rule_code（開關開著的）；非 withToggle（正式判決）恆全 7 支。 */
  const enabledCodes = computed(() =>
    opts.withToggle ? promptCodes.value.filter((c) => enabled.value[c]) : promptCodes.value,
  );

  const resolved = computed<ResolvedPromptSelection>(() => {
    const versions: Record<string, number> = {};
    for (const code of enabledCodes.value) {
      const sel = selected.value[code];
      if (typeof sel === 'number' && sel !== activeVersionOf(code)) {
        versions[code] = sel; // 等於 active 不必帶，維持請求精簡、沿用既有 cache 快路徑
      }
    }
    return { versions };
  });

  return { promptCodes, selected, enabled, enabledCodes, optionsFor, resolved, ensureLoaded };
}

// 7 條初判 prompt 的版本選擇（確認初判分類抽屜用）：每支 prompt 一個下拉，選項＝該 rule_code
// 全版本歷史（getRuleHistory）。resolved 只回「非 active」的指定版本——等於 active 不必帶，
// 讓請求維持精簡並沿用後端既有 cache 快路徑。
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

export function usePromptVersionPicker() {
  const store = useJudgeRulesStore();
  const historyByCode = ref<Record<string, RuleVersionMeta[]>>({});
  const selected = ref<Record<string, number>>({});

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
    }
  }

  function optionsFor(code: string): PromptVersionOption[] {
    return (historyByCode.value[code] || []).map((v) => ({
      value: v.version,
      label: versionLabel(v.created_at, v.version) + (v.is_active ? '（active）' : ''),
    }));
  }

  const resolved = computed<ResolvedPromptSelection>(() => {
    const versions: Record<string, number> = {};
    for (const code of promptCodes.value) {
      const sel = selected.value[code];
      if (typeof sel === 'number' && sel !== activeVersionOf(code)) {
        versions[code] = sel; // 等於 active 不必帶，維持請求精簡、沿用既有 cache 快路徑
      }
    }
    return { versions };
  });

  /** 重拉某 code 的版本歷史並把選中值對齊新 active（發版後呼叫：新版本要出現在下拉）。 */
  async function reloadHistory(code: string): Promise<void> {
    await store.loadList(); // 先刷新 metas 拿到新 active 版本號
    historyByCode.value[code] = await getRuleHistory(code);
    selected.value[code] = activeVersionOf(code) ?? 0;
  }

  return {
    promptCodes,
    selected,
    optionsFor,
    resolved,
    ensureLoaded,
    reloadHistory,
    activeVersionOf,
  };
}

/**
 * 規則 tab 的 config json 清單（manifest）：把 judge config 對映到「分組 › 面板」。
 *
 * 歸因分類（軸A：分類體系 + 預判）→ `config/taxonomy/`（attribution_tree/dimensions/symptoms/… + mappings/）。
 * `file` 僅用 basename（或 mappings/ 子路徑）；後端 /api/config 以「ai_judge 優先、taxonomy 回退」雙目錄解析，
 * 故搬檔後此清單零改。新增 config 只需加一筆，RulePanels 由本清單資料驅動渲染，免改模板。
 */

/** 單一 config 檔面板定義。 */
export interface RuleConfigEntry {
  /** basename 或 mappings/ 子路徑（如 'dimensions.json' / 'mappings/xxx.json'）；/api/config 雙目錄解析。 */
  file: string;
  /** 折疊面板標題。 */
  label: string;
  /** 標題旁灰字說明（一句話用途）。 */
  desc: string;
}

/** config 分組（對齊判決 pipeline 階段）。 */
export interface RuleConfigGroup {
  key: string;
  icon: string;
  title: string;
  entries: RuleConfigEntry[];
}

export const RULE_CONFIG_GROUPS: RuleConfigGroup[] = [
  {
    key: 'taxonomy',
    icon: '🏷️',
    title: '分類體系',
    entries: [
      {
        file: 'attribution_tree.json',
        label: '歸因分類體系（L1 域 › L2 面向 › L3 細項）',
        desc: '7 域三層 229 細項；規整開關 + 原始 JSON',
      },
      { file: 'dimensions.json', label: '內容面向定義', desc: '8 面向 → logical_field / rule_prefix' },
    ],
  },
  {
    key: 'presale',
    icon: '🧭',
    title: '預判規則（軸A · 進線 → 候選集）',
    entries: [
      { file: 'symptoms.json', label: '症狀樹 tag1 › tag2', desc: '進線症狀大類 / 候選集查表鍵' },
      { file: 'evidence_levels.json', label: '證據層級階梯', desc: 'ladder + satisfies 滿足關係' },
      {
        file: 'mappings/symptom_to_candidates.json',
        label: '症狀 → 候選歸因域',
        desc: 'tag2 → 候選域映射',
      },
      {
        file: 'mappings/review_tag_to_symptom.json',
        label: '評論面向 → 症狀',
        desc: 'ai_review tag → tag2',
      },
      {
        file: 'mappings/mixpanel_items.json',
        label: 'Mixpanel negative_items 映射',
        desc: 'negative_item → 症狀 / 候選域',
      },
      { file: 'mappings/keyword_heuristics.json', label: '關鍵字啟發式', desc: '進線關鍵字命中規則' },
    ],
  },
];

/** 歸因分類體系：保留既有規整開關 UI（與原始 JSON 並存），由 RulePanels 特例渲染。 */
export const TAXONOMY_TREE_FILE = 'attribution_tree.json';

/**
 * 規則 tab 的 config json 清單（manifest）：把 judge config 對映到「分組 › 面板」。
 *
 * 兩層配置（實體已切分）：
 *   判決規則（軸B）→ `config/ai_judge/`（verdicts/domains/judgment_chain/judgment_tiers/confidence，
 *     與 rule_C-* 及 schema 同層；後二者走 /api/judge-rules 版本化、不在本 raw 清單）。
 *   歸因分類（軸A：分類體系 + 預判）→ `config/taxonomy/`（attribution_tree/dimensions/symptoms/… + mappings/）。
 * `file` 僅用 basename（或 mappings/ 子路徑）；後端 /api/config 以「ai_judge 優先、taxonomy 回退」雙目錄解析，
 * 故搬檔後此清單零改。新增 config 只需加一筆，RulePanels 由本清單資料驅動渲染，免改模板。
 */

/** 單一 config 檔面板定義。 */
export interface RuleConfigEntry {
  /** basename 或 mappings/ 子路徑（如 'verdicts.json' / 'mappings/xxx.json'）；/api/config 雙目錄解析。 */
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
  {
    key: 'verdict',
    icon: '⚖️',
    title: '判決規則（軸B · 候選集 → 定論）',
    entries: [
      { file: 'judgment_chain.json', label: '判決鏈（互斥閘金字塔）', desc: 'gates 閘序 + 證據硬閘' },
      { file: 'domains.json', label: '歸因域定義', desc: '7 域責任方 / 預設 verdict / 最低證據' },
      { file: 'verdicts.json', label: 'verdict 定義', desc: '8 verdict 性質 / tier / 嚴重度' },
      { file: 'judgment_tiers.json', label: '判定層', desc: 'T1/T2/T3A/T3B/NP 與 owner' },
      { file: 'confidence.json', label: '信心封頂 + 分層閾值', desc: 'caps + auto/jury 閾值' },
      {
        file: 'mappings/force_majeure_keywords.json',
        label: '不可抗力關鍵字',
        desc: '閘0 force_majeure 命中詞',
      },
    ],
  },
];

/** 歸因分類體系：保留既有規整開關 UI（與原始 JSON 並存），由 RulePanels 特例渲染。 */
export const TAXONOMY_TREE_FILE = 'attribution_tree.json';

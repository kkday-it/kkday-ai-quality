# app/core/judge_config — 判準領域 config loader（package）

讀 `config/ai_judge`、`config/global` 的 JSON，提供判決引擎所需的判準/流程/映射。各 loader lazy load +
模組級快取（首次存取才載，編輯後 `reload()` 清快取）。`app/core/__init__` re-export 至 core 根層，
使 `from app.core import ai_judge` 等消費端零改動。

| loader | 讀取 | 提供 |
|---|---|---|
| `ai_judge.py` | `app.judge.prompt_source.structure()`（Prompt-as-Source，取代已退役的 DB rule_C-* 樹） | 分類結構索引（l3_nodes_for_domains / l3_by_code / domain_label / domain_action / domain_owner / cascade_tree…）；判準文字本體在 `prompts/*.md`，非本模組職責。2026-07-14 退役：`selectable_domains`/`domain_l2_labels`（漏斗時代零消費）+ `path_label`（隨標真值評分退役） |
| `product_vertical.py` | product_vertical 規則（DB active）| 商品垂直分類分組 → CATEGORY 代碼（codes_for_group）|
| `source_mapping.py` | DB active 版 source_mapping + config/ai_judge fallback | 5 來源欄位映射（源欄→canonical，normalize_row）+ 上傳指紋辨識／必備表頭校驗（RuleManager 線上編輯，存檔熱重載）|
| `sources.py` | config/global/sources.json | 來源目錄（label_for / natural_key）|
| `pricing.py` | config/global/llm_model.json | LLM per-model 單價（cost_usd）|
| `rule_export.py` | `app.judge.prompt_source.structure()` + judgment.json | 6 域面向結構 + judgment 判決配置 Excel 導出 + `_style_header`（品牌樣式，db.export 複用）|

> 2026-07-13：`ai_judge.py`/`rule_export.py` 隨 Prompt-as-Source 全面重構改讀 prompt 結構，不再讀 DB
> 規則樹（`rule_C-1`~`rule_C-6` + `schema`，含 canon/allow/forbid/正反例四欄判準面板）——判準已 100%
> 移入 `prompts/*.md`，歷史 DB 版本保留（不刪表）僅無新寫入路徑。同批退役：
> `rule_export.py` 的樹分頁邏輯（改面向清單）、`rule_refeed.py`（反哺飛輪，寫回對象已消失）。
> 同日（第二輪）：`global_rule.py`（極性閘門 polarity_gate + 證據政策 evidence_policy；先前已刪
> `attribution_guidance`/`polarity_guidance`/`abstain_policy`/`cascade`/`prejudge_depth`）整支併入
> `config/ai_judge/judgment.json`（靜態設定檔，改值需重啟後端）並移除，減少判決 config 檔案數；
> `prejudge.py` 改讀 `_evidence_policy()`/`_polarity_gate_cfg()`（皆委派 `_cfg()` 讀 judgment.json）。

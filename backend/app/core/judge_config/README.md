# app/core/judge_config — 判準領域 config loader（package）

讀 `config/ai_judge`、`config/global` 的 JSON，提供判決引擎所需的判準/流程/映射。各 loader lazy load +
模組級快取（首次存取才載，編輯後 `reload()` 清快取）。`app/core/__init__` re-export 至 core 根層，
使 `from app.core import ai_judge` 等消費端零改動。

| loader | 讀取 | 提供 |
|---|---|---|
| `ai_judge.py` | `app.judge.prompt_source.structure()`（Prompt-as-Source） | 分類結構索引（l2_by_code / l2_nodes_for_domains / domain_label / domain_action / domain_owner / evidence_gated_domains / cascade_tree…）；判準文字本體在 `prompts/*.md`，非本模組職責。 |
| `product_vertical.py` | product_vertical 規則（DB active）| 商品垂直分類分組 → CATEGORY 代碼（codes_for_group）|
| `source_mapping.py` | DB active 版 source_mapping + config/ai_judge fallback | 5 來源欄位映射（源欄→canonical，normalize_row）+ 上傳指紋辨識／必備表頭校驗（RuleManager 線上編輯，存檔熱重載）|
| `sources.py` | config/global/sources.json | 來源目錄（label_for / natural_key）|
| `pricing.py` | config/global/llm_model.json | LLM per-model 單價（cost_usd）|
| `rule_export.py` | `app.judge.prompt_source.structure()` + judgment.json | 6 域面向結構 + judgment 判決配置 Excel 導出 + `_style_header`（品牌樣式，db.export 複用）|

> `ai_judge.py`/`rule_export.py` 判準結構讀 `app.judge.prompt_source.structure()`（Prompt-as-Source），不讀 DB 規則樹；判準文字 100% 在 `prompts/*.md`。判決流程設定集中 `config/ai_judge/judgment.json`（靜態設定檔，改值需重啟後端）：`prejudge.py` 經 `_cfg()` 讀取 `_evidence_policy()`/`_polarity_gate_cfg()`。

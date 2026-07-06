# app/core/judge_config — 判準領域 config loader（package）

讀 `config/ai_judge`、`config/global` 的 JSON，提供判決引擎所需的判準/流程/映射。各 loader lazy load +
模組級快取（首次存取才載，編輯後 `reload()` 清快取）。`app/core/__init__` re-export 至 core 根層，
使 `from app.core import ai_judge` 等消費端零改動。

| loader | 讀取 | 提供 |
|---|---|---|
| `ai_judge.py` | DB active 版 rule_C-* + config/ai_judge fallback | 葉判準樹（selectable_domains / l3_nodes_for_domains / domain_action…）+ **L1 域／L2 面向分支判準**（`l1_judgment` / `l2_judgment`，供 cascade 分層界線注入）|
| `global_rule.py` | global_rule.json（DB active）| 判決總規範（decision_tree / cascade / abstain_policy / global_boundaries）|
| `product_vertical.py` | product_vertical 規則（DB active）| 商品垂直分類分組 → CATEGORY 代碼（codes_for_group）|
| `source_mapping.py` | source_mapping.json | 5 來源欄位映射（源欄→canonical，normalize_row）+ 上傳指紋辨識 |
| `sources.py` | config/global/sources.json | 來源目錄（label_for / natural_key）|
| `pricing.py` | config/global/llm_model.json | LLM per-model 單價（cost_usd）|
| `rule_export.py` | config/ai_judge 規則樹 | 判準規則 Excel 導出 + `_style_header`（品牌樣式，db.export 複用）|

# config — 前後端共用配置 SSOT（業務可調·非機密）

前後端**同讀同一份 JSON**：後端 `app.core.paths.CONFIG_DIR`（`GLOBAL_DIR`/`AI_JUDGE_DIR`），前端 `@config` alias。
機密走 `backend/.env`，固定參照字典走 `constants/`（見決策樹 `.claude/rules/config-and-hardcode.md`）。

## global/（業務可調·跨環境）
- `llm_model.json` — LLM providers 目錄 + defaultModels + per-model 單價（pricing loader / 前端下拉共用）
- `sources.json` — 5 來源 code → label / natural_key（sources loader）
- `product_vertical.json` — 商品垂直分類分組 → CATEGORY 代碼（product_vertical 默認 seed）
- `qc_db.json` — QC DB 連線預設

## ai_judge/（判準領域）
- `rule_C-1 ~ rule_C-6.json` — 6 歸因域 L1→L2→L3 厚判準樹（canon/allow/forbid/正反例）
- `rule.schema.json` — 規則樹結構規格（存前 jsonschema 驗證）
- `global_rule.json`（+`.schema`）— 判決總規範（決策樹 / cascade / abstain policy）
- `judgment.json` — 信心分層閾值 + 傾向/分層/判決階段中文 label + prejudge 旋鈕（前後端同讀；已納入 RULE_CODES＝可經 RuleManager 版本化編輯 + 存檔熱重載）
- `source_mapping.json`（+`.schema`）— 5 來源欄位映射（源欄→canonical）+ 上傳指紋辨識

> **live 真相在 DB**（`judge_rule_versions` append-only 版本化，經 RuleManager 面板編輯）；本目錄檔＝git 版控的**默認 seed**（恢復默認來源），非執行期讀取的 active 版。

# config — 前後端共用配置 SSOT（業務可調·非機密）

前後端**同讀同一份 JSON**：後端 `app.core.paths.CONFIG_DIR`（`GLOBAL_DIR`/`AI_JUDGE_DIR`），前端 `@config` alias。
機密走 `backend/.env`，固定參照字典走 `constants/`（見決策樹 `.claude/rules/config-and-hardcode.md`）。

## global/（業務可調·跨環境）
- `llm_model.json` — LLM providers 目錄 + defaultModels + per-model 單價（pricing loader / 前端下拉共用）
- `sources.json` — 5 來源 code → label / natural_key（sources loader）
- `product_vertical.json` — 商品垂直分類分組 → CATEGORY 代碼 + `group_order` 顯示順序（jsonb 不保 key 序，故顯式存；product_vertical 默認 seed）
- `qc_db.json` — QC DB 連線預設
- `roles.json` — 輕量 RBAC 白名單（admins email 清單 + defaultRole；後端 auth.role_for 每請求即時派生，改名單免重登入）
- `role_permissions.json` — 角色 → business-key 權限集合 SSOT（admin `["*"]` 全量、qc 質檢子集；key 定義見 `backend/app/core/permissions/permission_keys.py`；LocalPermissionProvider 讀此）
- `auth.config.json` — 權限 provider 切換開關（`provider`=local｜be2·唯一替換點）+ `whiteList`（be2 免權限路徑預留）+ `businessListTtlMs`（前端權限清單快取 TTL）

## ai_judge/（判準領域）
- `rule_C-1 ~ rule_C-6.json` — 6 歸因域 L1→L2→L3 厚判準樹（canon/allow/forbid/正反例）
- `rule.schema.json` — 規則樹結構規格（存前 jsonschema 驗證）
- `global_rule.json`（+`.schema`）— 判決總規範（極性閘門 attribute_when / `prejudge_depth`：初判深度——l3＝完整 L1→L3 cascade、l2＝只判 L1+L2 單呼叫 31 面向目錄（L3 留待接上商品/訂單佐證的深判，成本約 -51%）/ cascade 兩階段+低信心重路由 / abstain·證據政策 attr_min_confidence / 判官提示詞）；域界線 SSOT＝各 rule_C-N 的 L1 canon（舊 decision_tree/global_boundaries 已移除）
- `judgment.json` — 信心分層閾值 + 傾向/分層/判決階段/覆核狀態中文 label（`status_labels`：new/auto_confirmed/confirmed/dismissed；後端 `_shared._STATUS_LABEL_ZH` 有 code-side fallback 容忍舊 DB active 版缺鍵）+ prejudge 旋鈕（per-stage `*_reasoning_effort` 省 token 旋鈕：polarity／stage_a／attribute，null＝沿用主 config；`batch_service_tier`：批次判決 serving tier，"flex"＝OpenAI flex processing -50% 計價換變動延遲、429 自動回退標準、`flex_min_items` 以下小批走標準）+ **auto_confirm（G1 自動確認路由：enabled + audit_sample_rate）**（前後端同讀；**2026-07-13 起降為專案靜態設定檔**——移出 RULE_CODES、不再經 RuleManager 版本化編輯，改值＝改此檔 + 重啟後端）
- `source_mapping.json`（+`.schema`）— 5 來源欄位映射（源欄→canonical）+ 上傳指紋辨識／必備表頭校驗（已納入 RULE_CODES＝可經「規則配置 › 上傳表頭校驗」版本化編輯 + 存檔熱重載；本檔為初始 seed）
- `free_tag_mapping.json` — 外部評論系統 free_tag 面向名 → 我方歸因分類（現行 taxonomy L1/L2 label）語義映射 SSOT，供評論對比表判定 free_tag 與歸因是否契合（多對多）。⚠️ L1/L2 label 須與現行歸因規則同步；由 `scripts/tools/build_comparison_report.py` 讀取

## overview/（總覽儀表板）
- `dashboard.json` — 質檢概覽 config-驅動版面（views 分區 + charts catalog）；前端 `@config/overview/dashboard.json` 讀取（DashboardView），業務可調版面免改碼

> **live 真相在 DB**（`judge_rule_versions` append-only 版本化，經 RuleManager 面板編輯）；本目錄檔＝git 版控的**默認 seed**（恢復默認來源），非執行期讀取的 active 版。

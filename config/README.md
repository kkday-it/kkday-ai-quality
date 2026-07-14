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
> **判準文字唯一真相源已改為 `docs/prompts/*.md`**（Prompt-as-Source 架構，7 支：00_polarity +
> 01~06_C-N）。2026-07-13 全面重構：原 `rule_C-1 ~ rule_C-6.json`（L1→L2→L3 判準樹，canon/allow/forbid/
> 正反例四欄）+ `rule.schema.json`（樹結構規格）+ `prompt_templates/`（評測 Prompt 包骨架層）已退役刪除，
> 判準改由 LLM 直讀各域 prompt 的 System 區塊，分類結構（域機器值/L2 面向）由
> `app.judge.prompt_source.structure()` 從 prompt 派生（不再讀 DB 樹）。

- `domains.json` — 六域註冊表（domain 機器值 → 中文名 / recommended_action / owner）：唯一無法從 prompt md 推導的域層業務 metadata，與 prompt 檔名尾綴 + facet_catalog 共同組成 `structure()`
- `judgment.json` — 判決流程總配置（**專案靜態設定檔**，改值＝改此檔 + 重啟後端）：極性閘門（`polarity_gate.attribute_when`：哪些整體傾向進歸因）+ 證據政策（`evidence_policy`：`attr_min_confidence`/`secondary_min_confidence`/`require_quote_grounded` 閘門；2026-07-13 併入原 `global_rule.json`，減少判決 config 檔案數）+ 信心分層閾值 + 傾向/分層/判決階段/覆核狀態中文 label（`status_labels`：new/auto_confirmed/confirmed/dismissed；後端 `_shared._STATUS_LABEL_ZH` 有 code-side fallback 容忍舊 DB active 版缺鍵）+ prejudge 旋鈕（per-stage `*_reasoning_effort` 省 token 旋鈕：polarity／attribute，null＝沿用主 config；`batch_service_tier`：批次判決 serving tier，"flex"＝OpenAI flex processing -50% 計價換變動延遲、429 自動回退標準、`flex_min_items` 以下小批走標準）+ **auto_confirm（G1 自動確認路由：enabled + audit_sample_rate）**。判官提示詞與域界線已全數移入 prompt md，本檔不再存。已移出 RULE_CODES、不再經 RuleManager 版本化編輯。
- `source_mapping.json`（+`.schema`）— 5 來源欄位映射（源欄→canonical）+ 上傳指紋辨識／必備表頭校驗（已納入 RULE_CODES＝可經「規則配置 › 上傳表頭校驗」版本化編輯 + 存檔熱重載；本檔為初始 seed）

> `free_tag_mapping.json`（外部評論系統 free_tag 面向名 → 我方歸因分類映射）+ 讀取它的
> `app.core.db.comparison`/`scripts/tools/free_tag_coverage.py`/`build_comparison_report.py`/
> `multi_model_report.py`/`boundary_ab_eval.py`（+`_report.py`）已於 2026-07-13 全數移除。

## overview/（總覽儀表板）
- `dashboard.json` — 質檢概覽 config-驅動版面（views 分區 + charts catalog）；前端 `@config/overview/dashboard.json` 讀取（DashboardView），業務可調版面免改碼

> **live 真相在 DB**（`judge_rule_versions` append-only 版本化，經 RuleManager 面板編輯）；本目錄檔＝git 版控的**默認 seed**（恢復默認來源），非執行期讀取的 active 版。

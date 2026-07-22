# config — 前後端共用配置 SSOT（業務可調·非機密）

前後端**同讀同一份 JSON**：後端 `app.core.paths.CONFIG_DIR`（`GLOBAL_DIR`/`AI_JUDGE_DIR`），前端 `@config` alias。
機密走 `backend/.env`，固定參照字典走 `constants/`（見決策樹 `.claude/rules/config-and-hardcode.md`）。

## global/（業務可調·跨環境）
- `llm_model.json` — LLM providers 目錄 + defaultModels + per-model 單價（pricing loader / 前端下拉共用）+ 根層 `embeddings[]`（embedding 模型單價，域路由特徵用——僅計價、不進聊天模型下拉）+ 根層 `areas[]`（LLM 消費功能區清單：prejudge/prompt_debug/sandbox，`llm_area_defaults` 的 key 集合）+ `modelCapabilities`（per-model 可配參數能力覆寫；未登記者回退所屬 provider 的 `supportsThinking`/`reasoningEffortOptions`/`temperatureLockedWhenThinking`/`lockedTemperatureValue` 欄位，後端 `settings.model_capabilities_for()` 讀取）
- `sources.json` — 5 來源 code → label / natural_key（sources loader）
- `product_vertical.json` — 商品垂直分類分組 → CATEGORY 代碼 + `group_order` 顯示順序（jsonb 不保 key 序，故顯式存；product_vertical 默認 seed）
- `qc_db.json` — QC DB 連線預設
- `roles.json` — 輕量 RBAC 白名單（admins email 清單 + defaultRole；後端 auth.role_for 每請求即時派生，改名單免重登入）
- `role_permissions.json` — 角色 → business-key 權限集合 SSOT（admin `["*"]` 全量、qc 質檢子集；key 定義見 `backend/app/core/permissions/permission_keys.py`；LocalPermissionProvider 讀此）
- `auth.config.json` — 權限 provider 切換開關（`provider`=local｜be2·唯一替換點）+ `whiteList`（be2 免權限路徑預留）+ `businessListTtlMs`（前端權限清單快取 TTL）
- `export.json` — 導出行為配置（前端消費）：`gdrive_upload_folder_url`＝導出完成通知「打開 Google Drive 上傳」捷徑的**全域預設**共用資料夾 URL（使用者可於帳號抽屜「導出偏好」per-user 覆寫，存 user_settings；皆空＝退回個人 my-drive）

## ai_judge/（判準領域）
> **判準文字唯一真相源為 `prompts/*.md`**（Prompt-as-Source 架構，7 支：00_polarity +
> 01~06_C-N）。判準由 LLM 直讀各域 prompt 的 System 區塊，分類結構（域機器值/L2 面向）由
> `app.judge.prompt_source.structure()` 從 prompt 派生（不讀 DB 樹）。

- `prejudge.json` — **初判階段**配置（**專案靜態設定檔**，改值＝改此檔 + 重啟後端；與 `verdict.json` 由後端 `_read_stage_files()` 合併載入）：極性閘門（`polarity_gate.attribute_when`：哪些整體傾向進歸因）+ 證據政策（`evidence_policy`：`attr_min_confidence`/`secondary_min_confidence`/`require_quote_grounded` 閘門；證據閘域已移入各域 prompt `## Taxonomy` root evidence_gated）+ 信心分層閾值 + 傾向/分層/初判階段中文 label（`stage_labels`：judged=已初判/unjudged=未初判/pending_review=待複審）+ prejudge 旋鈕（per-stage `*_reasoning_effort` 省 token 旋鈕：polarity／attribute，null＝沿用主 config；`batch_service_tier`：批次初判 serving tier，"flex"＝OpenAI flex processing -50% 計價換變動延遲、429 自動回退標準、`flex_min_items` 以下小批走標準；`max_workers_by_model`/`max_workers_default`＝依生效 model 分設批次併發上限，切旗艦模型自動降併發，實際再與 env.prejudge_max_workers 硬天花板取 min）+ `batch_max_reasoning_effort`（批次 job reasoning_effort 硬上限，預設 medium——防 xhigh 診斷檔位誤用於全量批次）+ `domain_router`（embedding 域路由剪枝：enabled 預設 false／shadow_rate 影子抽樣／embedding_model／thresholds／always_on；權重檔 data/router/weights.json 由 scripts/tools/train_domain_router.py 產出，任何故障 fail-open 全域跑）。＋ `pii_mask`（PII 輸入端遮罩：送雲端 LLM 前於 `prejudge._text_of` 唯一出口套用，`rules[{name,pattern,replacement}]` 依序 regex 置換——email/台灣手機/國際碼/10 位以上連號；刻意不遮訂單號/OID（判決佐證要用）；`enabled=false` 停用；公司「含 PII 外送雲端 AI API 需資安確認」慣例的工程防護佐證）。判官提示詞與域界線已全數移入 prompt md，本檔不再存（診斷理由 reason/abstain_reason 僅 Prompt 測試沙盒 overlay 動態提供，production 不收集、不落庫）。不納入 RuleManager 版本化編輯範圍。
- `verdict.json` — **判決階段**配置（同為靜態設定檔，與 `prejudge.json` 合併載入）：**auto_confirm（G1 自動確認路由：enabled + audit_sample_rate，系統判決留痕 verdict_by='system:auto_confirm'）** + 判決狀態中文 label（`status_labels`：new=待判決/auto_confirmed=自動確認/confirmed=已確認/dismissed=已駁回；後端 `_shared._STATUS_LABEL_ZH` 有 code-side fallback 容忍缺鍵）。
- `evidence.json` — **訂單佐證（qc_evidence）子系統**旋鈕（靜態設定檔，純後端消費、前端不讀；改值重啟生效）：db（dbname/schema/connect·statement timeout/pool_size 併發上限/熔斷 threshold·cooldown）+ cache（order_ttl_hours 6·易變欄位短 TTL／product_ttl_days 30·版本鎖長 TTL（快取落 PG evidence_snapshot 表，TTL 懶清理））+ singleflight_wait_timeout_s + summary（lang_fallback／max_chars_per_field／max_total_chars——`prejudge._summarize_evidence` 摘要器截斷旋鈕，輸出兼作 evidence_citation 落庫）。`enabled=false` 一鍵停用佐證取數（判決全走無佐證降級）。
- `source_mapping.json`（+`.schema`）— 5 來源欄位映射（源欄→canonical）+ 上傳指紋辨識／必備表頭校驗（已納入 RULE_CODES＝可經「規則配置 › 上傳表頭校驗」版本化編輯 + 存檔熱重載；本檔為初始 seed）

## overview/（總覽儀表板）
- `dashboard.json` — 質檢概覽 config-驅動版面（views 分區 + charts catalog）；前端 `@config/overview/dashboard.json` 讀取（DashboardView），業務可調版面免改碼

> **live 真相在 DB**（`judge_rule_versions` append-only 版本化，經 RuleManager 面板編輯）；本目錄檔＝git 版控的**默認 seed**（恢復默認來源），非執行期讀取的 active 版。

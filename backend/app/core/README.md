# app/core — 領域基礎層

初判引擎（`app/judge`）與 API（`app/api`）共用的基礎設施：資料存取、判準 config、領域模型、設定。

## 結構
| 項目 | 職責 |
|---|---|
| `db/` | **資料存取層**（package）：`tables`（SQLAlchemy schema+engine）、`source_registry`（來源→表 SSOT）、`_shared`（共用 helper/常數），及 15 個職責模組（schema_bootstrap / settings_store / rule_versions / ingest / findings / qc_evidence / problems / prejudge_targets / attribution / attribution_history / prejudge_runs / llm_usage / export / export_stats / datapack）。`__init__` barrel re-export 全函式 → `from app.core import db; db.X()`。見 `db/README.md`。 |
| `judge_config/` | **判準 config loader**（package）：ai_judge / bd_tag_vertical / source_mapping / sources / pricing / rule_export，讀 `config/ai_judge`、`config/global` JSON。`app/core/__init__` re-export → `from app.core import ai_judge`（判準流程設定見 `judge_config/README.md`）。|
| `schema.py` | Pydantic 領域模型（`TicketFinding` 初判單元）；初判引擎與 db 兩側平行消費。 |
| `job_registry.py` | **in-mem job registry 共用機制層**（`JobStore`：dict+lock+快照深拷貝+終態掃描）——2026-07-23 從 export_jobs/import_jobs/judge.prejudge_batch/judge.ingest.upload_batch 等各自手刻的同型骨架收斂而來（composition，非強制繼承）；只管「怎麼安全存取一份 dict」，暫停/取消/AIMD 自適應併發等控制流留在各自呼叫端模組不進此基底。⚠️ 目前共 **6 個 `JobStore` 實例**（export / import / prejudge_batch / upload_batch / prompt_debug_batch / prompt_regression），但 `shutdown.mark_running_jobs_interrupted()` 只收尾前 5 套——`prompt_regression` 沒有 `mark_running_interrupted()`，其 job 在行程重啟後會永遠停在 `running`。 |
| `export_jobs.py` | 通用導出背景 job registry（`JobStore` + `ExportCtx`(report/check) + start/cancel/pop_result）；問題列表 / 初判規則導出共用，端點見 `api/routers/exports.py`。 |
| `import_jobs.py` | 資料包匯入背景 job registry（`JobStore` + `ImportCtx` 逐表回報進度）；與 `export_jobs.py` 對稱，端點見 `api/routers/admin_import.py`。 |
| `settings.py` | 全項目共享運行期設定（固定 `__global__` row，非 per-user）：LLM 連線層 `llm_connections`（per-provider base_url）+ 模型配置庫 `llm_model_configs`（全域配置扁平陣列，單層；`llm_model.json` 的 `modelConfigs` 只是全新環境初始內容）分離，**`name` 不落庫**、由 `derive_config_name()` 於讀取端衍生（`all_model_configs()`/`masked()`/`raw()` 三處投影），唯一性判 `spec_key()` 的規格重複、QC DB 同構 `qc_connections`、導出偏好 `gdrive_upload_folder_url`；CRUD + 遮罩 + 舊多套 config 結構一次性遷移；配置值域在寫入邊界校驗（`_validate_model_configs`，拋 `ValueError` → 路由轉 400），不做讀取端自癒；`effective_llm_dict(s, area=, overrides=)` 為 judge 路徑收斂點，**只吃 flat 旋鈕、不認識「配置」抽象**；落庫邊界呼叫 `crypto` 對機密 map（`llm_tokens`/`qc_passwords`）加解密。⚠️「哪個功能區用哪一筆配置」＝ `llm_area_configs`（area → config id），**同在 DB、團隊共用單一份**：功能區下拉選了就即時落庫（無儲存按鈕），一個人換配置同事下次進頁面也會是新的——這是 2026-07-31 拍板的取捨（換來「你調好的安排同事與新裝置能直接用到」），不是 bug。刪配置時同步剪除指向它的綁定；讀取端仍有「綁定 → 出廠 areaDefaults → 清單第一筆」三級回落。 |
| `crypto.py` | 機密 at-rest 加密（Fernet；key＝env `AIQ_SECRET_KEY`，未設明文直通可回滾）。密文帶 `enc:v1:` 前綴、舊明文值直通（重存即補加密）。 |
| `config.py` | env `Settings`（機密/跨環境值：DATABASE_URL / CORS / timeout / DB 連線池…），全專案最底層依賴。 |
| `logging_setup.py` | kklog 結構化 stdout 日誌（公司 Kibana/Filebeat 契約）：`KklogJsonFormatter`（單行 JSON·@timestamp 逐筆即時——Filebeat 停收坑迴歸鎖）+ `RequestContextMiddleware`（X-Request-Id 生成/沿用/回填→`request.uuid`）+ `configure_logging()`（dictConfig 接管 root+uvicorn 三支；access log 排除 `/api/status` probe 噪音）。`log_type`＝`config.env.log_type`（Kibana 查詢鍵）。 |
| `shutdown.py` | graceful shutdown 收尾：`mark_running_jobs_interrupted()` 彙總標記 5 套 in-mem registry（export/import/prejudge/upload/prompt_debug_batch）進行中 job 為 interrupted（main.py lifespan 唯一呼叫點）。timeout 鏈：uvicorn 30s < compose 35s < k8s 40s（三處互註解交叉引用）。 |
| `auth_verifiers.py` | be2 模式登入 verifier（去帳戶系統後僅 `authProvider=be2` 才介入·local 模式不經過此模組）：`get_verifier()` 回 `Be2TokenVerifier`（be2 accessToken claims decode＋exp＋email 直回身分·無本地 users 表；**驗簽 TODO 待 auth team 契約·production 啟用即拒**，`is_production()` 硬閘拋 RuntimeError）。 |
| `errors.py` | API 錯誤 code 統一入口 `raise_api_error(code, message, status_code)` → HTTPException(detail={code, message})。前端據 code 對映 i18n 翻譯（見前端 `src/i18n`）；漸進採用 touch-when-edit。 |
| `concurrency.py` | **LLM 批次任務的自適應併發原語**（`ConcurrencyGovernor` AIMD + `is_rate_limit`）——2026-07-31 從 `judge.prejudge_batch` 提取為共用層，初判批量與售後根因 Prompt 跑批共用同一顆 governor 與同一組 `prejudge.json adaptive_concurrency` 旋鈕。存在理由：**沒有任何供應商公布「併發數」這個維度**（OpenAI/Gemini/火山方舟都只公布 RPM/TPM 且綁帳號 tier），最佳併發只能執行期靠 429 自己探——樂觀起於 ceiling、撞 429 乘性收縮至 floor、清空後加性回升。純 threading，零外部依賴。 |
| `paths.py` | 路徑 SSOT（REPO_ROOT / CONFIG_DIR / AI_JUDGE_DIR / GLOBAL_DIR），全專案唯一算一次。 |
| `auth.py` | 帳號身分解析（去帳戶系統 2026-07-22：無 register/login/登出/切換帳號/bcrypt/自建 JWT）：`get_current_user` 本地模式回固定身分 `SYSTEM_USER='system'`（不驗 token；沒有 SSO 就沒有經過驗證的人，稽核欄記 system 而非假 email，可由 env `LOCAL_USER_EMAIL` 覆寫供 grants 顆粒測試）；`actor(user)` 是稽核欄取身分的唯一出口（無 email 即回 `SYSTEM_USER`）；be2 模式驗 Bearer token（走 `auth_verifiers.get_verifier()`）。身分僅供權限授予查詢與稽核欄位，不是存取控制手段——控制交給下方 `permissions/`。 |
| `permissions/` | **可替換權限框架**（package·無角色，直接授予）：`PermissionProvider` 抽象（base）+ `require_permission(key)` dependency（deps）+ business-key 常數（permission_keys，be2 風格 `module.sub-function.action`）+ `LocalPermissionProvider`（email 對照 `config/global/permissions.json`，`default ∪ grants[email]`，`no_auth_grant_all=true` 現行全通過）+ `Be2PermissionProvider`（過渡委派 LocalPermissionProvider，行為安全等價）。換 be2 中央 Auth SVC 唯一改動點＝`config/global/auth.config.json['authProvider']` + `be2_provider.py` 改打 Auth SVC，router 全不動。fail-closed（permissions.json 缺/壞→空集合）。 |
| `flags.py` | OpenFeature 初判閾值旗標介面（`threshold()`）+ 薄 `JudgeConfigProvider`（解析 judge.<tier> → judgment confidence_tiers，DB active·熱重載）。面向 OpenFeature 標準避供應商鎖定，Phase 7 換 Flagsmith 呼叫端零改；prejudge 閾值讀取走此。 |

## 依賴方向（禁循環）
`config`/`paths` 為底層；`db`/`judge_config` 依 tables+config；`settings` 不可被 `db` 反向 import（db 註解明載）。判準 loader 讀 DB 的 active 版走延遲 import（`from app.core import db`）避循環。`flags` 讀 judgment 配置亦走延遲 `from app.core.db import _shared`。

# app/core — 領域基礎層

判決引擎（`app/judge`）與 API（`app/api`）共用的基礎設施：資料存取、判準 config、領域模型、設定。

## 結構
| 項目 | 職責 |
|---|---|
| `db/` | **資料存取層**（package）：`tables`（SQLAlchemy schema+engine）、`source_registry`（來源→表 SSOT）、`_shared`（共用 helper/常數），及 8 個職責模組（users / rule_versions / ingest / findings / problems / prejudge_targets / attribution / export）。`__init__` barrel re-export 全函式 → `from app.core import db; db.X()`。見 `db/README.md`。 |
| `judge_config/` | **判準 config loader**（package）：ai_judge / global_rule / product_vertical / source_mapping / sources / pricing / rule_export，讀 `config/ai_judge`、`config/global` JSON。`app/core/__init__` re-export → `from app.core import ai_judge`。 |
| `schema.py` | Pydantic 領域模型（`TicketFinding` 判決單元 + `AdequacyResult`）；判決引擎與 db 兩側平行消費。 |
| `export_jobs.py` | 通用導出背景 job registry（in-mem 進度快照 + `ExportCtx`(report/check) + start/cancel/pop_result）；問題列表 / 判決規則導出共用，端點見 `api/routers/exports.py`。 |
| `settings.py` | 使用者運行期設定（LLM/QC 連線 profiles、啟用狀態）CRUD + 遮罩 + 遷移；落庫邊界呼叫 `crypto` 對機密 map 加解密。 |
| `crypto.py` | 機密 at-rest 加密（Fernet；key＝env `AIQ_SECRET_KEY`，未設明文直通可回滾）。密文帶 `enc:v1:` 前綴、舊明文列直通；既有列遷移用 `scripts/tools/encrypt_user_secrets.py`。 |
| `config.py` | env `Settings`（機密/跨環境值：DATABASE_URL / CORS / timeout / DB 連線池…），全專案最底層依賴。 |
| `errors.py` | API 錯誤 code 統一入口 `raise_api_error(code, message, status_code)` → HTTPException(detail={code, message})。前端據 code 對映 i18n 翻譯（見前端 `src/i18n`）；漸進採用 touch-when-edit。 |
| `paths.py` | 路徑 SSOT（REPO_ROOT / CONFIG_DIR / AI_JUDGE_DIR / GLOBAL_DIR），全專案唯一算一次。 |
| `auth.py` | JWT 簽發/驗證 + 密碼雜湊 + 角色派生（`role_for`：角色由 `config/global/roles.json` 白名單每請求即時派生，admin/qc 兩級、零 migration）。正式環境缺/弱 JWT secret（<32 bytes）拒啟動。端點授權改由 `permissions/` 負責（見下）。 |
| `permissions/` | **可替換權限框架**（package）：`PermissionProvider` 抽象（base）+ `require_permission(key)` dependency（deps）+ business-key 常數（permission_keys，be2 風格 `module.sub-function.action`）+ `LocalPermissionProvider`（角色→key 讀 `config/global/role_permissions.json`）+ `Be2PermissionProvider` 空殼。換 be2 中央 Auth SVC 唯一改動點＝`config/global/auth.config.json['provider']` + `be2_provider.py`，router 全不動。fail-closed。 |
| `flags.py` | OpenFeature 判決閾值旗標介面（`threshold()`）+ 薄 `JudgeConfigProvider`（解析 judge.<tier> → judgment confidence_tiers，DB active·熱重載）。面向 OpenFeature 標準避供應商鎖定，Phase 7 換 Flagsmith 呼叫端零改；prejudge 閾值讀取走此。 |

## 依賴方向（禁循環）
`config`/`paths` 為底層；`db`/`judge_config` 依 tables+config；`settings` 不可被 `db` 反向 import（db 註解明載）。判準 loader 讀 DB 的 active 版走延遲 import（`from app.core import db`）避循環。`flags` 讀 judgment 配置亦走延遲 `from app.core.db import _shared`。

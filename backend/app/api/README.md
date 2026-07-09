# app/api — FastAPI gateway

HTTP 邊界層：路由 → 委派 `app/core/db` + `app/judge`。薄層（業務邏輯下沉 core/judge），
啟動時 `db.init_db()`（create_all，冪等）+ `db.seed_rules_from_files()`。

| 項目 | 內容 |
|---|---|
| `main.py` | 僅 app 組裝：CORS + `db.init_db()`/`seed_rules_from_files()` + 掛載全部 router（端點實作分散於 `routers/`，各自帶完整 /api 路徑）。 |
| `routers/auth.py` | 帳號系統（/api/auth：register / login / me / **permissions**）。GET `/api/auth/permissions` 回當前 user 的 business-key 權限清單（be2 `auth.business-list` 契約形狀 `{value, ttl, startTime}`，供前端 v-auth / 選單 / 守衛）。 |
| `routers/inbound.py` | 資料錄入（/api/inbound：validate / upload / upload/stream SSE）+ 批次清單（/api/batches）。 |
| `routers/settings.py` | 設定（/api/settings：get/update/raw/test-llm）+ QC DB 連線測試（/api/datasource/qc-db/test）；含 `load_user_context` 守衛 + `_activate_settings`（contextvar 注入 judge 路徑）。 |
| `routers/findings.py` | 單筆歸因人工動作（PATCH /api/findings/{id}/status｜/true_label + 真值把關 evaluate + 備註 notes + 級聯樹 taxonomy-cascade，**需登入**·記操作者/時間 audit）。 |
| `routers/problems.py` | 統一問題列表 / 縱覽聚合（/api/problems*）+ 列表導出（POST /api/problems/export → 背景 job）。 |
| `routers/v1/` | `judgment.py`（初判歸因批次：prejudge 啟動/筆數預覽 POST `/prejudge/count`（與啟動同一套標的解析，可 `within_ids` 交集勾選範圍）/SSE 串流/暫停/恢復/停止 + 歸因歷史 GET `/runs`·`/runs/{job_id}`——run 級 LLM 使用紀錄，執行中列 overlay in-mem 即時進度）；`__init__` 聚合於 `/api/v1`。 |
| `routers/config.py` | config JSON 線上編輯（讀寫 config/ai_judge，寫後 reload loader）。 |
| `routers/rules.py` | 判決規則版本化 CRUD（/api/judge-rules：list/active/history/save/restore/reset + jsonschema 驗證）；POST `/export` 啟動規則 xlsx 導出背景 job。**寫入端點（save/restore/reset×2）掛 `require_permission(judge-rule.version.manage)`（admin 級·403）**。 |
| `routers/exports.py` | 通用導出 job 端點（/api/exports：SSE `stream` 進度 / `download` 取檔 / `cancel` 停止），搭 `app/core/export_jobs` 全域 registry，問題列表 / 判決規則導出共用。 |
| `routers/overview.py` | 質檢概覽真實指標（GET /api/overview/ai-judge：judgments 內容類占比月趨勢 + 總量；「縮窄真接」——外部系統指標不在此，前端維持示意）。 |
| `routers/admin_import.py` | 全庫資料包導出/匯入（/api/admin：POST `/export/start` 啟動導出背景 job〔逐表進度，復用通用 export_jobs，下載走 /api/exports/download〕；POST `/import/validate` 乾跑校驗、POST `/import` 確認匯入背景 job、GET `/import/stream` SSE），委派 `app/core/db/datapack` + `app/core/import_jobs` + `app/core/export_jobs`。匯入只灌白名單表·不執行 SQL；環境閘 `AIQ_ALLOW_DATA_IMPORT`（dev 開）。授權：匯入掛 `require_permission(data.datapack.import)`（admin 級）、導出 `data.datapack.export`（qc+admin），經可替換權限框架（`app/core/permissions`）判定。 |

> 認證：JWT（Bearer header）；capability-token 端點（prejudge / 導出 SSE `stream` / import `stream`）以 job_id 免 header，其餘（cancel / download / export / import）仍需 Bearer。
> 授權：破壞性端點掛 `require_permission(key)`（`app/core/permissions`）——rules/config write＝admin 級（`judge-rule.version.manage`/`config.file.write`）、datapack import＝`data.datapack.import`（admin）；findings 覆核 / 標真值、inbound 上傳、problems 導出、datapack 導出＝qc+admin 級。角色→key 映射見 `config/global/role_permissions.json`；provider 切換見 `auth.config.json`。

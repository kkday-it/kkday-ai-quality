# app/api — FastAPI gateway

HTTP 邊界層：路由 → 委派 `app/core/db` + `app/judge`。薄層（業務邏輯下沉 core/judge），
啟動時 `db.init_db()`（create_all，冪等）+ `db.seed_rules_from_files()`。

| 項目 | 內容 |
|---|---|
| `main.py` | 僅 app 組裝：CORS + `db.init_db()`/`seed_rules_from_files()` + 掛載全部 router（端點實作分散於 `routers/`，各自帶完整 /api 路徑）。 |
| `routers/auth.py` | 帳號系統（/api/auth：register / login / me）。 |
| `routers/inbound.py` | 資料錄入（/api/inbound：validate / upload / upload/stream SSE）+ 批次清單（/api/batches）。 |
| `routers/settings.py` | 設定（/api/settings：get/update/raw/test-llm）+ QC DB 連線測試（/api/datasource/qc-db/test）；含 `load_user_context` 守衛 + `_activate_settings`（contextvar 注入 judge 路徑）。 |
| `routers/findings.py` | 判決結果（/api/findings、/api/products）+ 單筆歸因人工動作（PATCH /api/findings/{id}/status｜/true_label）。 |
| `routers/problems.py` | 統一問題列表 / 縱覽聚合（/api/problems*）+ 列表導出（POST /api/problems/export → 背景 job）。 |
| `routers/v1/` | `judgment.py`（初判歸因批次：prejudge 啟動/SSE 串流/暫停/恢復/停止）；`__init__` 聚合於 `/api/v1`。 |
| `routers/config.py` | config JSON 線上編輯（讀寫 config/ai_judge，寫後 reload loader）。 |
| `routers/rules.py` | 判決規則版本化 CRUD（/api/judge-rules：list/active/history/save/restore/reset + jsonschema 驗證）；POST `/export` 啟動規則 xlsx 導出背景 job。 |
| `routers/exports.py` | 通用導出 job 端點（/api/exports：SSE `stream` 進度 / `download` 取檔 / `cancel` 停止），搭 `app/core/export_jobs` 全域 registry，問題列表 / 判決規則導出共用。 |

> 認證：JWT（Bearer header）；capability-token 端點（prejudge / 導出 SSE `stream`）以 job_id 免 header，其餘（cancel / download）仍需 Bearer。

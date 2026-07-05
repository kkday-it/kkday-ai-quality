# app/api — FastAPI gateway

HTTP 邊界層：路由 → 委派 `app/core/db` + `app/judge`。薄層（業務邏輯下沉 core/judge），
啟動時 `db.init_db()`（create_all，冪等）+ `db.seed_rules_from_files()`。

| 項目 | 內容 |
|---|---|
| `main.py` | app 實例 + CORS + 掛載 routers；帳號（/api/auth）、上傳（/api/inbound/{validate,upload}）、問題列表/縱覽（/api/problems*）、問題列表導出（POST /api/problems/export → 背景 job）、findings/products、單筆歸因人工動作（PATCH /api/findings/{id}/status｜/true_label，歸因列表操作欄用）、settings、datasource 端點。 |
| `routers/v1/` | `judgment.py`（初判歸因批次：prejudge 啟動/SSE 串流/暫停/恢復/停止）；`__init__` 聚合於 `/api/v1`。 |
| `routers/config.py` | config JSON 線上編輯（讀寫 config/ai_judge，寫後 reload loader）。 |
| `routers/rules.py` | 判決規則版本化 CRUD（/api/judge-rules：list/active/history/save/restore/reset + jsonschema 驗證）；POST `/export` 啟動規則 xlsx 導出背景 job。 |
| `routers/exports.py` | 通用導出 job 端點（/api/exports：SSE `stream` 進度 / `download` 取檔 / `cancel` 停止），搭 `app/core/export_jobs` 全域 registry，問題列表 / 判決規則導出共用。 |

> 認證：JWT（Bearer header）；capability-token 端點（prejudge / 導出 SSE `stream`）以 job_id 免 header，其餘（cancel / download）仍需 Bearer。

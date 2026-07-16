# app/api — FastAPI gateway

HTTP 邊界層：路由 → 委派 `app/core/db` + `app/judge`。薄層（業務邏輯下沉 core/judge），
啟動時 `db.init_db()`（create_all，冪等）+ `db.seed_rules_from_files()`。

| 項目 | 內容 |
|---|---|
| `main.py` | 僅 app 組裝：CORS + `db.init_db()`/`seed_rules_from_files()` + 掛載全部 router（端點實作分散於 `routers/`，各自帶完整 /api 路徑）。 |
| `routers/auth.py` | 帳號系統（/api/auth：register / login / me / **permissions**）。GET `/api/auth/permissions` 回當前 user 的 business-key 權限清單（be2 `auth.business-list` 契約形狀 `{value, ttl, startTime}`，供前端 v-auth / 選單 / 守衛）。 |
| `routers/inbound.py` | 資料錄入（/api/inbound：validate / upload / upload/stream SSE）+ 批次清單（/api/batches）。 |
| `routers/settings.py` | 設定（/api/settings：get/update/raw/test-llm）+ QC DB 連線測試（/api/datasource/qc-db/test）；含 `load_user_context` 守衛 + `_activate_settings`（contextvar 注入 judge 路徑）。 |
| `routers/findings.py` | 歸因人工動作（PATCH /api/findings/{id}/status 單筆覆核〔confirmed/dismissed/new＝撤銷·同值冪等〕｜PATCH /api/findings/batch/status 批量覆核〔勾選評論的全部歸因，單交易 diff〕｜備註 notes｜級聯樹 GET /api/findings/taxonomy-cascade〔L1→L2 巢狀，供歸因列表篩選 cascader〕，**需登入**·記操作者/時間 audit）+ 評論級判決歷史（GET /api/judgment-history 時間軸〔judgment/status/note 三類事件〕、POST /api/judgment-history/notes 評論級備註、GET /api/judgment-history/models 歷來判決過的模型清單〔篩選/導出下拉〕）。 |
| `routers/problems.py` | 統一問題列表 / 縱覽聚合（/api/problems*，皆支援 model CSV 多選＝判決模型篩選·當前判決維度）+ 列表導出（POST /api/problems/export → 背景 job；`snapshot_model`＝輸出結果版本〔內容替換為該模型 judgment_history 最新快照〕；`compare_models`＝並排對比模型多選〔基準右側每模型附一組情緒/L1/L2 欄，值取該模型最新快照〕——兩者語義獨立可並用）。 |
| `routers/v1/` | `judgment.py`（初判歸因批次：prejudge 啟動/筆數預覽 POST `/prejudge/count`（與啟動同一套標的解析，可 `within_ids` 交集勾選範圍）/SSE 串流/暫停/恢復/停止 + 歸因歷史 GET `/runs`·`/runs/{job_id}`——run 級 LLM 使用紀錄，執行中列 overlay in-mem 即時進度；歸因列表「Prompt 測試」沙盒——`PromptSandboxIn` 繼承 `PrejudgeIn`（item_ids 顯式優先，否則 scope="all" 依 stages 依條件批量選取，零改動重用 `_resolve_target_ids`）：POST `/prompt-sandbox`（勾選 prompt 子集 ungated 跑，回 job_id）/ POST `/prompt-sandbox/count`（筆數預覽，與啟動同一套標的解析）/ GET `/prompt-sandbox/status`（輪詢進度）/ GET `/prompt-sandbox/runs`·`/prompt-sandbox/runs/{run_id}`（測試歷史列表/詳情，含完整 LLM log 快照回看，與正式初判歷史完全分離；stub 模式無條件拒跑，dev 亦不例外）；`__init__` 聚合於 `/api/v1`。 |
| `routers/rules.py` | 判決規則版本化 CRUD（/api/judge-rules：list/active/history/save/restore/reset + jsonschema 驗證）；POST `/export` 啟動判決 Prompt 包 zip 導出背景 job。**寫入端點（save/restore/reset×2）掛 `require_permission(judge-rule.version.manage)`（admin 級·403）**。 |
| `routers/exports.py` | 通用導出 job 端點（/api/exports：SSE `stream` 進度 / `download` 取檔 / `cancel` 停止），搭 `app/core/export_jobs` 全域 registry，問題列表 / Prompt 包 / 資料包導出共用。 |
| `routers/overview.py` | 質檢概覽真實指標（GET /api/overview/ai-judge：judgments 內容類占比月趨勢 + 總量；「縮窄真接」——外部系統指標不在此，前端維持示意）。 |
| `routers/admin_import.py` | 全庫資料包導出/匯入（/api/admin：POST `/export/start` 啟動導出背景 job〔逐表進度，復用通用 export_jobs，下載走 /api/exports/download〕；POST `/import/validate` 乾跑校驗、POST `/import` 確認匯入背景 job、GET `/import/stream` SSE），委派 `app/core/db/datapack` + `app/core/import_jobs` + `app/core/export_jobs`。匯入只灌白名單表·不執行 SQL；環境閘 `AIQ_ALLOW_DATA_IMPORT`（dev 開）。授權：匯入 `data.datapack.import`、導出 `data.datapack.export`——兩者 qc+admin 皆有（登入即可用），經可替換權限框架（`app/core/permissions`）判定，日後要收緊只改 `role_permissions.json`。 |

> 認證：JWT（Bearer header）；capability-token 端點（prejudge / 導出 SSE `stream` / import `stream`）以 job_id 免 header，其餘（cancel / download / export / import）仍需 Bearer。
> 授權：破壞性端點掛 `require_permission(key)`（`app/core/permissions`）——rules write＝admin 級（`judge-rule.version.manage`）；findings 覆核、inbound 上傳、problems 導出、datapack 導出/匯入、批量初判啟停（`judgment.prejudge.run`，消耗 LLM 額度）＝qc+admin 級（登入即可用）；problems / overview 讀端點掛登入最低門檻（`get_current_user`）。角色→key 映射見 `config/global/role_permissions.json`；provider 切換見 `auth.config.json`。

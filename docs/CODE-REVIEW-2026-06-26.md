# 代碼全方位審查與修正（2026-06-26）

> 4 agent 平行審查（架構 / 後端正確性 / 靜默失敗 / 前端品質）。本次直接修了 7 個安全高價值問題；其餘行為性 / 重構性問題列「待修」供後面繼續。

## ✅ 已修（本次，本地未 push，皆驗證 compile+import / vue-tsc 通過）

| # | 問題 | 修法 | 檔案 |
|---|---|---|---|
| 1 | 預設 model `gpt-5-mini` 不在 config 清單且被 modelMinVersion 過濾 → **新用戶設 key 後首判全 count=0 靜默失敗** | 改 `gpt-5.4-mini`（對齊 SSOT） | config.py:35 · settings.py:38 |
| 2 | `_DIM_FIELD` 費用/集合 fallback → `prod_summary`（錯欄位）→ 判決比對錯內容 | 費用→`prod_fee`、集合→`prod_meetup` | pipeline.py:44-45 |
| 3 | `PATCH /findings/{id}/status` 接任意字串 → DB 狀態漂移 | `StatusIn.status: Literal["confirmed","dismissed","fixed"]`（422 擋非法） | main.py:353 |
| 4 | `list_models()` 自訂 base_url 找不到 provider → 誤回 OpenAI 清單 | 有 base_url 但無對應 provider → 回 `[]` | client.py |
| 5 | `chat_json` LLM 回非 JSON → 炸穿 / 無 log | try `json.loads`，失敗 log warning + 降級 `{}`（API 錯誤仍上拋供計數） | client.py |
| 6 | `diagnose_many` `print` 吞錯、失敗筆數不可見 | `print`→`logging.error(exc_info=True)`；endpoint 回 `failed_count` | pipeline.py · main.py:215 |
| 7 | `FindingCard.setStatus` 點按鈕失敗**完全無回饋** | try/catch + `Message.success/error` | FindingCard.vue:27 |

## ❌ 誤報（agent 報但實為 by-design，不動）

- `roster.load_merged` 讀 `r.get("prod_schedule")`/`r.get("prod_exchange")`（architect 報 P0 拼字錯誤）→ **roster.py:211 註解明寫 CSV 欄名就是 `prod_schedule`/`prod_exchange`，刻意映射到 db `prod_schedules`/`prod_redeem`**。待對真實 merged CSV 表頭確認命名，暫不動。

## 🔜 待修（後面繼續，依優先序）

### A. 行為性 P0/P1（屬判決鏈，建議與「6 源來源匯總」一起做，需 golden 驗證）
- **`data_missing` 從未觸發**（silent-failure P0-3）：`fetch_product` 回 `{}` 不區分「找不到 vs 真空欄」→ 商品缺失時每筆判 `content_missing` 信心 0.9 假陽性洪水。需 fetch_product 回 None/flag，pipeline 設 `status=data_missing` 跳過判決。
- **整批 LLM 故障靜默 count=0**（silent-failure P0-1/2/4）：API 錯誤(auth/rate-limit/timeout) 冒泡被 diagnose_many 吞 → 需寫 `inbound_items.status="failed"`、整批失敗率高升 503。
- `_from_live`（product/reviews）無 timeout 分類 + 仍打公網（datadome 風險，reviews.py/product.py）。
- `load_user_settings` JSON 損壞靜默回 None → 整 session 降 stub（db.py，加 log）。
- `_sanitize_classify` confidence/dimension 非法靜默補位無 log → prompt drift 不可偵測（pipeline.py，加 warning）。
- `arbiter` content_unclear 0.6 兜底無 log（arbiter.py）。
- codex 法典檔缺失 runtime 才炸 → app startup preflight check（codex.py）。

### B. 整合 / 一致性（接來源匯總前處理）
- `inbound_items.status` 判決後不回寫（永遠 pending）→ dashboard 無法知哪些判過/失敗（pipeline+db+endpoint）。
- `schema.py LogicalField` Literal 6 欄 vs `pipeline._LOGICAL` 11 欄脫節 → 補齊或顯式改 str + 註解。
- `diagnose/conversations` source=live `raise NotImplementedError` → 500（應接成 501 友善訊息）。
- `entry.py source_channel` 沒填 → dashboard 管道分類無資料。

### C. 死代碼 / 未接線
- `ingestion/`+`repositories/` import 不存在的 `app.ingestion.base`/`app.models` → **v1 router 取消註解即 ImportError 爆**。需補 base/models 或標 WIP guard。
- `vendored_review/prodtag_router` + compliance_prompts 完整實作但零接線（待 codex.adequacy_criteria 接類目路由）。
- `vendored/writer_prompts/`（description/highlights/product_name）屬上游 writer 資產，法官不用 → 移 archive/ + MANIFEST 註記。
- `vendored/judge_prompts/時效過期_GEN1.md` 未登記 codex `_DEEP_*`（待時效子類）。

### D. 架構重構
- `api/main.py` 18+ 端點 750 行 → 拆 `routers/{auth,ingest,judge}.py`。
- `insert_inbound_batch` 每筆開新連線 → 單連線 executemany。
- `smoke_test.py` 缺 auth → `/api/diagnose` 回 401 全失敗（補 register/login 取 token）。

### E. 前端 typed-refactor
- `http.api.ts j()` 泛型化 `j<T>()` → 消除多數隱式 any。
- 建 `FindingRow` type 進 `packages/types`，收斂 `FindingCard f:any` / `finding.util.flatFinding any` / `Analytics·ProductDetail ref<any[]>`。
- 抽 `extractMessage(e:unknown)` util（10+ 處 `catch(e:any)`）。
- `ProductDetail.loadFindings` 補 try/catch（下拉切換商品失敗無回饋）。
- `FindingCard` 改 `emit('statusChanged')` 取代直接 mutate prop（單向資料流）。

## 亮點（agent 確認，無需改）
- 判決主鏈 pipeline→classify→adequacy→arbiter→diagnose→codex 邏輯清晰、依賴單向、stub/real 雙模式良好。
- 安全乾淨：動態 SQL 全參數化、`_sanitize_oids` isdigit 白名單防注入、ping 錯誤截斷不洩 key。
- 前端：Arco 元件無自造輪子、Tailwind utility-first 徹底、StateGuard 三態統一、storage.util 封裝副作用、api barrel 清晰。

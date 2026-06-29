# 架構梳理 + 配置抽離分析報告（2026-06-26）

> 3 agent 平行掃描（後端硬code / 前端硬code / 架構分層）。多 agent 交叉確認者標 ★★★（高信心）。
> 目標：公共配置單一真相源（不前後端兩套）、消除硬 code、可配置化。**本報告先行，動手前確認。**

## 一、最高槓桿（多 agent 交叉確認，建議優先）

### P0-1 ★★★ `ACTIONABLE_VERDICTS` **四份**定義散落
「什麼 verdict 算內容問題」這個業務核心判斷，竟有 4 份：
- `backend/app/core/schema.py:48`（SSOT）
- `backend/app/core/roster.py:54`（`_ACTIONABLE` 另寫一份）
- `backend/app/core/db.py:315`（SQL 字串內硬寫 `'real_config_issue','content_missing','content_unclear'`）
- `frontend/packages/types/src/finding.ts:36`
**風險**：任一 verdict 改名 → 4 處要同步，已知漏改地雷。
**修**：後端 roster/db 一律 `import schema.ACTIONABLE_VERDICTS`（消 3→1）；前端從 config 讀（見 P1-1）。改動 ~3 檔。

### P0-2 ★★★ WIP 死代碼會「靜默崩潰」
`app/ingestion/`（7 parser）+ `app/repositories/`（4 repo）import **不存在的** `app.ingestion.base` / `app.models`。
- 目前 main.py 沒 import 它們 → 運行正常；但**任何人取消 v1 router 註解或直接 import → 後端立即崩潰**，且無測試、無 import guard、執行前不可見。
**修（二選一）**：① 補 `app/ingestion/base.py`（定義 `RawRecord`/`ParsedItem`）+ `app/models.py` 讓它可用；② 標 `_WIP` + README 說明，或移 `archive/`。改動 ~11 檔。**建議先 ②（標記隔離）**，等 ingestion 真要接線再 ①。

### P1-1 ★★★ 信心度門檻 `0.85 / 0.7` **三處**各自寫死
同一「高/中信心分界」語義散在：`arbiter.py:41,49`、`FindingCard.vue:21`、`Analytics.vue:50,183`。
**修**：`config/defaults.json` 新增 `judge.confidence: {hi:0.85, mid:0.7}`，前後端各讀。改動 ~3 檔 + config。

## 二、配置抽離（前後端兩套 → config SSOT）

| 項 | 現況（兩套） | 建議 | 信心 |
|---|---|---|---|
| ★★★ dimension 8 名 | `schema.py:14`(Literal) / `pipeline.py:32`(_VALID_DIMS) / `roster.py:36`(DIM_CODE) / 前端 `finding.ts:4` | config `dimensions` 陣列當資料 SSOT；後端 pipeline/roster 從 schema 衍生（消內部 3 份）；前後端各讀 config + CI 校驗 | ✅ |
| ★★★ verdict 6 值 | `schema.py:37` / 前端 `finding.ts:25` | config `verdicts` + `actionableVerdicts` 陣列 | ✅ |
| ⚠️ 預設 model | 後端 `settings.py:38`/`config.py:35` 寫死 `gpt-5.4-mini` / 前端 `provider.constant.ts:69` fallback 字面量 | 後端 `_DEFAULT["model"]` 改讀 `LLM_PROVIDERS[0].defaultModel`（已有 LLM_PROVIDERS）；前端 fallback 同 | ✅ |
| ⚠️ status 5 態 | `schema.py:129` / 前端 `status.constant.ts` | config `statuses` 陣列（label/color 仍前端） | ⚠️ |
| ⚠️ source/channel | 前端 4 值 vs 後端 schema 5 值（**已不對齊**）| config `sources`/`channels` | ⚠️ |
| ⚠️ RecommendedAction | 後端有 `penalize_breach`/`rewrite_field`，前端 `ACTION_LABEL` **缺漏** → raw key 顯示 | 補前端 label + config 對齊 | ✅ |

## 三、硬 code 抽離（非重複，但該配置化）

| 項 | 位置 | 建議去處 | 信心 |
|---|---|---|---|
| ★★★ CORS origin `localhost:5173` | `main.py:31` | `backend/.env` `CORS_ALLOW_ORIGINS`（部署換 domain 免改碼）| ✅ |
| arbiter 信心常數 0.4/0.7/0.8/0.85/0.9 | `arbiter.py` 各 return | config `judge.confidence` 區段 | ✅ |
| 商品名長度門檻 60/35/×1.2 | `machine_checks.py:76,80,88` | config `content_rules.name_length` | ✅ |
| 外部 API URL（B2C/評論）| `product.py:104`/`reviews.py:62` | `backend/.env`（live 才用，已有 TODO 換內網）| ✅ |
| BigQuery project `kkday-data-dap` | `product_refresh.py:146` | `backend/.env` `BIGQUERY_PROJECT_ID` | ✅ |
| httpx timeout 30（兩處）/ QC DB timeout 5 | `product.py`/`reviews.py`/`main.py:423` | config `api.http_timeout` | ✅ |
| JWT TTL 7 天 / 密碼最短 6 | `auth.py:24`/`main.py:74` | `backend/.env` / config `security` | ⚠️ |
| 前端 port 8100/5273 | `vite.config.ts:19,27` | `.env.development` `VITE_*` | ✅ |
| ★ `ProductDetail.vue:12` `prodId=ref('150665')` | UI state 寫死真實 prod_oid | 改 `ref('')`，dev 預設移 `.env.development` | ✅ |
| 分頁 size 10/15/20 散落 | Analytics/DataUpload | 前端 constants `PAGE_SIZE_*` | ⚠️ |

## 四、結構分層（architect 評 2.5/5）

- **main.py 503 行上帝路由**（auth/inbound/diagnose/settings/datasource/model 六職責）→ 拆 `routers/v1/{auth,inbound,judge,settings}.py`（routers/ scaffold 已在）。P1，~6 檔。
- **roster.py 三職責**（schema migration + 載入 + 聚合）→ 拆 3 模組。P1，~3 檔。
- **db.py 383 行上帝模組**（5 表 CRUD）→ 長期往 repositories/ 靠攏（但 repositories/ 現死）。P2。
- **兩套 ingest 並存**：`app/ingestion/`(新 dlt) vs `judge/ingest/`(主力)→ 標 Phase + README 說明合流計畫。P2。
- 死代碼：`app/schemas/sources/`（mixpanel/review_summary 無人 import）→ 標 TODO 或併 ingestion/。P3。

## 五、不該抽（避免過度配置化，architect 判準）

保留程式邏輯、勿進 config：JWT 演算法 HS256、bcrypt 72 bytes（演算法固定）、stub 啟發式信心值（開發用）、`_DIM_FIELD`/`DIM_CODE` 路由表結構（但 key 引用 schema 常數）、Tailwind UI 色彩（前端職責）、verdict UI 描述文字（前端 i18n 範疇）。

## 六、建議執行批次（價值/風險比排序）

| 批 | 內容 | 改動 | 風險 |
|---|---|---|---|
| **A**（建議先做）| P0-1 ACTIONABLE_VERDICTS 收 SSOT + P0-2 WIP 死代碼標記隔離 | ~5 檔 | 低（消地雷）|
| **B** | CORS/timeout/BigQuery/JWT 抽 `backend/.env` + .env.example | ~6 檔 | 低 |
| **C** | 信心度門檻 + dimension/verdict 進 config，前後端各讀 | ~8 檔 | 中（需 golden 驗判決不變）|
| **D** | main.py 拆 routers + roster.py 拆職責 | ~12 檔 | 中（純搬移，需 smoke）|
| **E** | 前端：ProductDetail 清 sample、分頁常數、port 進 .env | ~5 檔 | 低 |

> C/D 動到判決鏈與大搬移，建議與「6 源來源匯總架構」一起做、配 golden 驗證。A/B/E 獨立安全，可立即做。

## 對應
報告依據：3 agent 掃描（2026-06-26）。SSOT＝`config/defaults.json`。相關：`CODE-REVIEW-2026-06-26.md`。

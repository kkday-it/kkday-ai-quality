# kkday-ai-quality — 技術棧與「輪子」推薦清單

> 目標：盡量複用成熟輪子，**提高 AI 法官判斷準確性**，減少自造與重寫。
> 基礎技術棧：**前端 Vue3（Node 工具鏈）+ 後端 Python**。
> 構想來源：Gary（Slack）——用 **function calling** 讓 AI 自主決定呼叫哪個 API 撈資料（function1 order API、function2 評論 API、function3 商品 API…）。Python 的 OpenAI SDK / Pydantic AI 同樣成熟。
> 邏輯參照 folder 2117435397（L0–L5）；查證日 2026-06-22。

## 基礎技術棧（三者角色）
| 層 | 技術 | 角色 |
|---|---|---|
| 前端框架 | **Vue3** + Vite + TS | dashboard |
| 前端工具鏈 | **Node**（Vite / pnpm / dev server）| 建置 runtime，**非後端** |
| 後端 | **Python**（FastAPI）| 判決引擎，沿用 ProductContentAIChecker 已驗證資產 |

## 選型總表（一句話結論）

| 環節 | 首選輪子 | 套件 | 為何（提高準確性的關鍵） |
|---|---|---|---|
| 後端框架 | **FastAPI** | `fastapi` `uvicorn` | 沿用既有 repo 慣例、async、自動 OpenAPI（前端 contract）|
| LLM SDK + function calling | **OpenAI Python SDK** | `openai` | `tools`(function calling)+`response_format json_schema strict`；Gary 構想的 function1/2/3 自選 |
| 結構化輸出/驗證 | **Pydantic v2**（+ 選用 `instructor`）| `pydantic` `instructor` | 判決 schema（TicketFinding/verdict）型別安全 + 自動 retry；model 層 strict schema |
| 判決邏輯 | **沿用 ProductContentAIChecker** | — | rules.json / prompt / 雙意見仲裁 / golden / optimizer 直接複用（**零重寫、保留 F1 0.986 準確率**）|
| 評估 / golden / LLM-as-judge | **Promptfoo** + **DeepEval**（py 原生）| `promptfoo` `deepeval` | `llm-rubric`/`g-eval`/**multi-judge voting**；信心度 calibration + 準確率驗收 |
| 可觀測 + token 成本監控 | **Langfuse**（py SDK）| `langfuse` | trace + 分專案 token 成本（對應獨立 key 計量）|
| 存儲（MVP→prod）| SQLite → PostgreSQL + **pgvector** | `sqlite3` / `psycopg` `pgvector` | Finding 持久化；pgvector 供同類爭議語義聚類/去重 |
| 前端框架 | **Vue3** + Vite | `vue` `vite` | 指定 |
| 前端 UI | **Arco Design Vue** | `@arco-design/web-vue` | 字節企業級、data-heavy 強（table/descriptions/tag）、原生 Vue3、維護中（2.58 / 2026-04）|
| 圖表 | **vue-echarts** | `vue-echarts` `echarts` | dimension×verdict 熱力矩陣 + 趨勢 sparkline |
| 前端狀態 | **Pinia** | `pinia` | dashboard 狀態 |

## 為何後端用 Python（不換 Node）
1. **零重寫、保留已驗證準確率**：判決精華（rules/prompt/雙意見仲裁/golden/eval/optimizer）全在 ProductContentAIChecker Python 且已驗證（GEN-1 F1 0.986 零誤判）。換 Node＝重寫＋重驗準確率（真風險）。
2. **三支柱同語言共用基建**：審品/撰寫也是 Python，rules.json/golden/optimizer 可直接 import。
3. **function calling 非 Node 專屬**：OpenAI Python SDK / Pydantic AI / instructor 一樣成熟，Gary 構想可直接做。
4. **批次跑數據強**：pandas + 既有 batch 腳本複用（多模型/多語系驗收）。
- 代價：前後端雙語言，介面靠 FastAPI 自動 OpenAPI + `frontend/src/types/finding.ts` ↔ `backend/app/schema.py` 對齊。

## Function Calling 設計（OpenAI SDK tools = Python functions）
| tool | 作用 | 來源（已驗證）|
|---|---|---|
| `fetch_reviews(prod_id, sort, page)` | 差評（RATING_ASC 優先）| 本 session 已驗證（150665）|
| `fetch_product(prod_id)` + `extract_fields` | 商品 9 欄原文 | api-b2c CDN + 固定 header |
| `fetch_order(order_id)` | 訂單詳情（履約事實）| 待 order 權限（Gary）|
| `fetch_ticket(ticket_id)` | 工單 + 客服對話 | 6/30 工單 API |

## 沿用 vs 重寫（既有 Python repo → 新 repo）
- **後端幾乎全沿用 Python**：fork/import 判決邏輯、rules、prompt、商品/評論拉取、golden/eval。
- **前端全新**：Vue3 + Arco + ECharts（dashboard 本來就要新做）。
- **概念沿用**：兩階段、雙意見仲裁、verdict 五分類、信心度路由、L0–L5、兩出口。

## 待拍板選型分叉
1. 後端 fork 形態：獨立 `backend/`（已選）vs 在既有 repo 加 judge 模組。
2. 結構化輸出：純 OpenAI strict schema vs 加 `instructor`（retry/coercion）。建議加 instructor。
3. 存儲：MVP SQLite vs 直接 PG+pgvector。建議 MVP SQLite。
4. 評估：Promptfoo（CI 快）+ DeepEval（py 深度）並用。

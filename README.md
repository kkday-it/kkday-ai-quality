# kkday-ai-product-quality

**AI 商品質檢平台 — AI 法官（內容爭議裁決系統）**（KKday 內容質量 Pod 第三支柱）

把客訴 / 商品差評 / 工單等真實負面訊號，自動歸因到「哪個商品的哪個欄位該改」，產出可執行 action，並反推「哪條審核規則最該優先」。目標：**降低售後進線的內容類占比**。

> 邏輯參照 [folder 2117435397](https://kkday.atlassian.net/wiki/spaces/VM/folder/2117435397)（工單驅動審核 L0–L5）+ [AI 法官 V2 總覽](https://kkday.atlassian.net/wiki/spaces/VM/pages/2125660181)。

## 技術棧
- **後端**：Python 3.10+ / FastAPI（沿用 ProductContentAIChecker 判決資產）+ OpenAI SDK（function calling）+ Pydantic + SQLite
- **前端**：Vue3 + Vite + **Arco Design** + vue-echarts + Pinia（M3 實作）
- **LLM**：OpenAI gpt-5-mini（無 key 時走 stub 啟發式）
- 選型/輪子清單見 [docs/TECH-STACK.md](./docs/TECH-STACK.md)；架構見 [ARCHITECTURE.md](./ARCHITECTURE.md)；各方面 SD 見 [docs/specs/](./docs/specs/)

## Monorepo 結構
```
backend/                 # Python（FastAPI）
  pyproject.toml · requirements.txt
  fixtures/product_150665.json   # golden 測試資料（纜車案例）
  app/
    core/      schema.py(Pydantic) · db.py(SQLite)
    judge/     ingest/entry.py · datasource/{reviews,product}.py
               classify · adequacy · arbiter · diagnose · pipeline · llm/client.py
    api/main.py                    # FastAPI gateway
frontend/                # pnpm workspace（Vue3+Arco+ECharts，M3）
  apps/console/ · packages/{types,ui,charts}
docs/                    # ARCHITECTURE · TECH-STACK · DELIVERY-PLAN · specs/00–04
```

## 🚀 啟動流程

### 後端（已可用）
```bash
cd backend

# 1. 建虛擬環境（首次）
python3 -m venv .venv

# 2. 安裝依賴（首次 / requirements 變動時）
.venv/bin/pip install -e .

# 3. 啟動 API（port 8100，--reload 開發熱重載）
.venv/bin/uvicorn app.api.main:app --reload --port 8100
```
> 需 Python ≥ 3.10。DB 為 SQLite，自動建於 `backend/data/aiqc.db`（gitignore）。

### LLM 模式
- **無 `OPENAI_API_KEY`**：自動走 **stub**（啟發式），可零 key 走通整條 pipeline。
- **設 key**（6/25 生效後）：`export OPENAI_API_KEY=sk-...`（可選 `export AI_JUDGE_MODEL=gpt-5-mini`），自動切真 LLM。

### 驗證（另開終端）
```bash
# 健康檢查
curl http://localhost:8100/health
# → {"status":"ok"}

# 評論線判決（fixture 模式，150665 富士山一日遊）
curl -X POST http://localhost:8100/api/diagnose \
  -H 'Content-Type: application/json' -d '{"prod_oid":"150665"}'
# → count=6，纜車案例判 content_unclear / 承諾與SLA

# 查判決結果
curl 'http://localhost:8100/api/findings?prod_oid=150665'

# CSV/Excel 批量錄入（表頭含 prod_oid,rating,comment，中英別名容錯）
curl -F file=@your.csv http://localhost:8100/api/inbound/upload

# 單個錄入
curl -X POST http://localhost:8100/api/inbound \
  -H 'Content-Type: application/json' \
  -d '{"prod_oid":"150665","comment":"客訴文字","rating":1}'

# 查錄入清單
curl http://localhost:8100/api/inbound
```
> API 文件（Swagger UI）：啟動後開 http://localhost:8100/docs

### 前端（M3 待實作）
目前為 scaffold（`frontend/apps/console` 僅 package.json + 共用 types），尚缺 `vite.config.ts`/`index.html`/`App.vue`。M3 補齊後：
```bash
cd frontend && pnpm install && pnpm dev   # （M3 後可用）
```

## API 一覽
| method | path | 說明 |
|---|---|---|
| GET | `/health` | 健康檢查 |
| POST | `/api/inbound/upload` | CSV/Excel 批量錄入 → SQLite |
| POST | `/api/inbound` | 單個錄入 |
| GET | `/api/inbound` | 錄入清單（`?status=`）|
| POST | `/api/diagnose` | 評論線判決（`{prod_oid, source}`，source=fixture/live）|
| GET | `/api/findings` | 判決結果（`?prod_oid=`）|

## 狀態（2026-06-23）
- ✅ M1a 錄入層（CSV/Excel/單個 → SQLite）
- ✅ M1b 資料拉取（fetch_reviews/fetch_product，fixture+live）
- ✅ M2-stub 判決層（評論線端到端走通，150665 纜車案例判對，stub 粗判 5/6）
- 🟡 M2 真 LLM + golden 驗收（等 OpenAI key 6/25）
- ⬜ M3 Dashboard（Arco+ECharts 兩出口 + 導入導出）· M4 閉環 · P2 多管道

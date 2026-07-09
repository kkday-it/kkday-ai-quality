# kkday-ai-quality

**AI 商品質檢平台 — AI 法官（內容爭議裁決系統）**（KKday 內容質量 Pod 第三支柱）

把客訴 / 商品差評 / 工單 / App 反饋 / 埋點等真實負面訊號，自動**歸因**到「哪個歸因域的哪個問題」（L1→L2→L3 三層分類），標註信心與判決階段，產出可執行方向，並反推「哪條審核規則最該優先」。目標：**降低售後進線的內容類占比**。

> 邏輯參照 [folder 2117435397](https://kkday.atlassian.net/wiki/spaces/VM/folder/2117435397)（工單驅動審核）+ [AI 法官 V2 總覽](https://kkday.atlassian.net/wiki/spaces/VM/pages/2125660181)。

## 技術棧
- **後端**：Python 3.10+ / FastAPI + SQLAlchemy Core + **PostgreSQL**（Alembic 遷移）+ OpenAI SDK（Structured Outputs）+ Pydantic
- **前端**：Vue3 + Vite + **Arco Design Vue** + Pinia + vue-echarts
- **LLM**：OpenAI gpt-5 系列（無 key 時走 stub 啟發式，可零 key 走通閉環）
- **啟動 / 部署**：**Docker Compose**——一鍵 `./scripts/dev/start.sh`（dev·hot reload）／生產 `docker compose up`（nginx + 多 worker）。**本機只需 Docker**，PG 與所有依賴皆在容器內。
- 文檔地圖見 [docs/README.md](./docs/README.md)（歷史選型與早期 spec 已封存於 `docs/archive/`，僅供追溯非現行契約）

## 核心流程
```
資料上傳（5 來源 xlsx/csv·自動辨識+校驗）
  → 各來源專表（product_reviews / conversations / freshdesk_tickets / app_feedback / mixpanel_tracker）
  → 初判歸因（prejudge：極性閘門 → 候選域 canon 聚焦 → L1/L2/L3 + 信心 + 判決階段；
     global_rule.prejudge_depth="l2" 時只判 L1+L2——L3 留待接上商品/訂單佐證的深判階段）
  → judgments（1:N 多歸因：一則評論可判多條獨立歸因，各自一列）
  → 歸因列表 / 歸因概覽（KPI+漏斗+趨勢）/ 美化 xlsx 導出
判準來源＝config/ai_judge 規則樹（RuleManager 面板版本化編輯，DB append-only 快照）
```

## Monorepo 結構
```
backend/                     # Python（FastAPI + SQLAlchemy + PostgreSQL）
  app/
    core/                    # 領域基礎層（見 core/README.md）
      db/                    # 資料存取（tables/source_registry + 8 職責模組 + barrel）
      judge_config/          # 7 判準 config loader（ai_judge/global_rule/…）
      auth · config · paths · schema · settings
    judge/                   # 判決引擎（prejudge 初判歸因 + batch 編排 + ingest 上傳 + llm client）
    api/                     # FastAPI gateway（main.py + routers/v1）
  alembic/                   # schema 遷移
  tests/ · fixtures/
config/                      # 前後端共用非機密配置 SSOT（global/ + ai_judge/）
constants/                   # 固定參照常數字典 SSOT（labels/）
frontend/                    # pnpm workspace（Vue3+Arco+ECharts）
  apps/console/src/features/ # judge / settings / overview / auth（feature-based）
  packages/types
scripts/                     # 開發腳本（dev/ · audit/ · tools/，見 scripts/README.md）
docs/                        # 文檔地圖（README）· 上手指南 HTML · archive/（過時 spec 封存）
docker-compose.yml           # 生產編排（PG + backend 多worker + frontend nginx）
docker-compose.dev.yml       # 開發編排（hot reload：source volume + uvicorn --reload + vite HMR）
backend/Dockerfile · frontend/Dockerfile · frontend/Dockerfile.dev   # 各服務 image
```

## 🚀 啟動

### 一鍵啟動（純 Docker，推薦）
```bash
./scripts/dev/start.sh
```
- **本機只需 Docker**（無需裝 python/node/pnpm/PostgreSQL）。start.sh 會**偵測 Docker → 未啟動則自動啟動並等待 → `docker compose up`** 起全服務。
- 全服務在容器內：**PostgreSQL + 後端 http://localhost:8100（Swagger `/docs`）+ 前端 http://localhost:5273 + 所有依賴**。
- **改碼即生效**（後端 `uvicorn --reload`、前端 vite HMR，掛 source volume）；首次會 build image（較久），之後秒起。**Ctrl-C** 停止所有服務。改依賴時 `docker compose -f docker-compose.dev.yml up --build` 重建。

> **容器引擎推薦 [colima](https://github.com/abiosoft/colima)（免費開源，大公司免授權）**：`brew install colima docker docker-compose`（macOS）｜Linux 直接 `sudo apt install docker.io docker-compose-plugin`。start.sh 會**優先自動 `colima start`**（無則退 OrbStack / Docker Desktop）。三者皆可，不強制裝商業版。

**載入全部數據（本地上傳，推薦）**：起來後在登入頁註冊帳號 → 「配置 › 資料導入」上傳**資料包 zip**（維護者自產：`python scripts/tools/dump_datapack.py` → `data/exports/kkday-ai-quality-datapack-*.zip`，或前台「導出資料包」下載）→ 校驗 → 輸入 `REPLACE-ALL-DATA` → 匯入。完整圖解見 [`docs/kkday-ai-quality-onboarding.html`](./docs/kkday-ai-quality-onboarding.html)。**空庫也能起**，資料靠上傳補上；或設 `SEED_URL` 由 db 首啟自動還原。

**生產部署**（多 worker + nginx 靜態；前端 http://localhost:8080）：
```bash
AIQ_JWT_SECRET=<your-secret> docker compose up -d --build   # 走 docker-compose.yml（生產）
```

**常用腳本**（見 [`scripts/README.md`](./scripts/README.md)）：`./scripts/dev/start.sh` 一鍵 Docker 啟動 · `python scripts/tools/dump_datapack.py` 產資料包 · `./scripts/dev/dump-seed.sh` 產 seed · `./scripts/dev/lint.sh` lint · `./scripts/dev/format.sh` format。

### 進階：不使用 Docker 手動跑（選用）
> 一般用上面 Docker 一鍵即可。若要在本機直接跑，需自備 Python ≥ 3.10 / Node ≥ 20 / pnpm / 本機 PostgreSQL（`kkdb_ai_quality`）：
```bash
# 後端
cd backend && python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/uvicorn app.api.main:app --reload --port 8100
# 前端（另開終端）
cd frontend && pnpm install && cd apps/console && npx vite   # :5273，dev proxy /api → :8100
```
> schema：dev 啟動時 `create_all` 建表並自動 stamp alembic head（資料包導入 schema 檢查需對齊）；連線經 `backend/.env` `DATABASE_URL`（預設 `postgresql+psycopg2://localhost:5432/kkdb_ai_quality`）。

### LLM 模式
- **無 `OPENAI_API_KEY`**：走 **stub**（啟發式），零 key 走通整條 pipeline。
- **有 key**：面板（設定 › LLM 模型連線）或 env `OPENAI_API_KEY` 設定，自動切真 LLM。

## API 一覽（主要）
| method | path | 說明 |
|---|---|---|
| GET | `/health` | 健康檢查 |
| POST | `/api/inbound/validate`·`/upload` | 上傳乾跑校驗 → 背景落各來源專表 |
| GET | `/api/problems` | 統一問題列表（source 專表 + 歸因，伺服器端分頁）；篩選：傾向/判決階段(多選)/信心分層/歸因分類(taxonomy 多選·任意層級 code 子樹語義)/日期區間/prod·order_oid |
| GET | `/api/problems/attribution_overview`·`/attribution_breakdown` | 歸因概覽聚合 + L2/L3 下鑽 |
| GET | `/api/overview/ai-judge` | 質檢概覽首頁 AI 法官真實指標（內容類占比月趨勢·distinct 進線；外部指標維持示意）|
| POST | `/api/problems/export` | 啟動問題列表 xlsx 導出背景 job（1:N 多歸因合併儲存格）→ {job_id} |
| POST | `/api/judge-rules/export` | 啟動判決規則 xlsx 導出背景 job → {job_id} |
| GET/POST | `/api/exports/{stream,download,cancel}` | 通用導出 job：SSE 實時進度 / 取檔 / 停止（跨導出共用）|
| POST/GET | `/api/v1/judgment/prejudge/*` | 初判歸因批次（啟動/筆數預覽 count/SSE 進度/暫停/恢復/停止；目標選取可 within_ids 交集勾選範圍）|
| GET | `/api/v1/judgment/runs` · `/runs/{job_id}` | 歸因歷史（run 級 LLM 使用紀錄：批量/選取/單筆重判；詳情含 per-stage token/費用明細）|
| CRUD | `/api/judge-rules/*` | 判決規則版本化（面板編輯/歷史/恢復默認/導出）|
| GET | `/api/products` · `/api/findings` | 商品清單（依 finding）· 判決結果列表 |
| PATCH | `/api/findings/{id}/status` · `/{id}/true_label` | 單筆歸因人工覆核（確認/忽略/已修）· 標註真值分類（歸因列表操作欄用）|
| POST | `/api/auth/register`·`/login` | 帳號 |
| POST | `/api/admin/export/start` | 啟動全庫資料包導出背景 job（逐表 SSE 進度）→ {job_id}；進度/下載走通用 `/api/exports/{stream,download}`。`include_sensitive` 才含 users/user_settings |
| POST/GET | `/api/admin/import{,/validate,/stream}` | 全庫資料包安全匯入（只灌白名單表·不執行 SQL）：乾跑校驗 → 確認匯入背景 job → SSE 進度。⚠️ admin 閘延後，現為登入即可 + `AIQ_ALLOW_DATA_IMPORT` 環境閘 |

> 完整 API：啟動後開 Swagger UI http://localhost:8100/docs

## 架構要點
- **5 來源各自獨立表**（對齊源 schema、以特徵 id 為鍵），judgments 以 `(source, source_id)` 關聯回來源表；canonical 顯示欄由 `config/ai_judge/source_mapping.json` 統一還原。
- **1:N 多歸因**：一則負向評論可判出多條獨立歸因（各自 finding_id、L1-L3、信心、判決階段），列表右側堆疊呈現、導出 fan-out。
- **判準 SSOT**＝`config/ai_judge` 規則樹（RuleManager 面板版本化，DB `judge_rule_versions` append-only 快照）。
- **配置化 SSOT**：機密 → `backend/.env`；前後端共用非機密 → `config/`（業務可調）/ `constants/`（固定字典）。
- **LLM 成本三重防線**：OpenAI prompt caching（靜態判準前綴）+ flex serving tier（批次 -50%，judgment 配置可關）+ **exact-match 結果快取**（`data/llm_cache`；重判時規則未變動部分零 token 重用，顯式單筆重判不吃快取）。
- **機密 at-rest 加密**：`user_settings` 的 provider_tokens / qc_passwords 以 Fernet 加密落庫（`app/core/crypto.py`，key＝env `AIQ_SECRET_KEY`；既有明文列遷移用 `scripts/tools/encrypt_user_secrets.py`）。`/api/settings/raw` 明文回顯僅回本人設定（JWT 守衛），屬**單機內網環境的有意識權衡**，部署公網前必須移除。

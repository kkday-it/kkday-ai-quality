# kkday-ai-quality

**AI 商品質檢平台 — AI 法官（內容爭議裁決系統）**（KKday 內容質量 Pod 第三支柱）

把客訴 / 商品差評 / 工單 / App 反饋 / 埋點等真實負面訊號，自動**歸因**到「哪個歸因域的哪個問題」（L1→L2→L3 三層分類），標註信心與判決階段，產出可執行方向，並反推「哪條審核規則最該優先」。目標：**降低售後進線的內容類占比**。

> 邏輯參照 [folder 2117435397](https://kkday.atlassian.net/wiki/spaces/VM/folder/2117435397)（工單驅動審核）+ [AI 法官 V2 總覽](https://kkday.atlassian.net/wiki/spaces/VM/pages/2125660181)。

## 技術棧
- **後端**：Python 3.10+ / FastAPI + SQLAlchemy Core + **PostgreSQL**（Alembic 遷移）+ OpenAI SDK（Structured Outputs）+ Pydantic
- **前端**：Vue3 + Vite + **Arco Design Vue** + Pinia + vue-echarts
- **LLM**：OpenAI gpt-5 系列（無 key 時走 stub 啟發式，可零 key 走通閉環）
- 文檔地圖見 [docs/README.md](./docs/README.md)（歷史選型與早期 spec 已封存於 `docs/archive/`，僅供追溯非現行契約）

## 核心流程
```
資料上傳（5 來源 xlsx/csv·自動辨識+校驗）
  → 各來源專表（product_reviews / conversations / freshdesk_tickets / app_feedback / mixpanel_tracker）
  → 初判歸因（prejudge：極性閘門 → 候選域 canon 聚焦 → L1/L2/L3 + 信心 + 判決階段）
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
docs/                        # 文檔地圖（README）· UPSTREAM-REFS · archive/（過時 spec 封存）
```

## 🚀 啟動

### 一鍵起前後端（推薦）
```bash
./scripts/dev/dev.sh
```
- 後端 → http://localhost:8100（Swagger `/docs`）｜前端 → http://localhost:5273
- **Ctrl-C 一次**前後端一起停；首次較慢（自建 venv + 裝依賴，前端需先 `pnpm install`）；需 `pnpm`

**常用腳本**（見 [`scripts/README.md`](./scripts/README.md)）：`./scripts/dev/dev.sh` 起前後端 · `./scripts/dev/seed.sh` 重置 mock · `./scripts/dev/test.sh` smoke · `./scripts/dev/lint.sh` lint · `./scripts/dev/format.sh` format · `./scripts/dev/doctor.sh` 環境自檢。

### 後端（單獨）
```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/uvicorn app.api.main:app --reload --port 8100
```
> 需 Python ≥ 3.10 + 本機 PostgreSQL（`kkdb_ai_quality`）。schema：dev 由啟動時 `create_all` 建表；prod 部署跑 `alembic upgrade head`。連線經 `backend/.env` `DATABASE_URL`（預設 `postgresql+psycopg2://localhost:5432/kkdb_ai_quality`）。

### 前端（單獨）
```bash
cd frontend && pnpm install
cd apps/console && npx vite   # http://localhost:5273（需後端先起於 8100；dev 經 vite proxy /api → 8100）
```

### LLM 模式
- **無 `OPENAI_API_KEY`**：走 **stub**（啟發式），零 key 走通整條 pipeline。
- **有 key**：面板（設定 › LLM 模型連線）或 env `OPENAI_API_KEY` 設定，自動切真 LLM。

## API 一覽（主要）
| method | path | 說明 |
|---|---|---|
| GET | `/health` | 健康檢查 |
| POST | `/api/inbound/validate`·`/upload` | 上傳乾跑校驗 → 背景落各來源專表 |
| GET | `/api/problems` | 統一問題列表（source 專表 + 歸因，伺服器端分頁）；篩選：傾向/判決階段(多選)/星等/信心分層/L1域/日期區間/prod·order_oid |
| GET | `/api/problems/l1_domains` | 某來源已判 L1 歸因域清單（[{code,label,count}]，供列表 L1 篩選下拉）|
| GET | `/api/problems/attribution_overview`·`/attribution_breakdown` | 歸因概覽聚合 + L2/L3 下鑽 |
| GET | `/api/overview/ai-judge` | 質檢概覽首頁 AI 法官真實指標（內容類占比月趨勢·distinct 進線；外部指標維持示意）|
| POST | `/api/problems/export` | 啟動問題列表 xlsx 導出背景 job（1:N 多歸因合併儲存格）→ {job_id} |
| POST | `/api/judge-rules/export` | 啟動判決規則 xlsx 導出背景 job → {job_id} |
| GET/POST | `/api/exports/{stream,download,cancel}` | 通用導出 job：SSE 實時進度 / 取檔 / 停止（跨導出共用）|
| POST/GET | `/api/v1/judgment/prejudge/*` | 初判歸因批次（啟動/SSE 進度/暫停/恢復/停止）|
| GET | `/api/v1/judgment/runs` · `/runs/{job_id}` | 歸因歷史（run 級 LLM 使用紀錄：批量/選取/單筆重判；詳情含 per-stage token/費用明細）|
| CRUD | `/api/judge-rules/*` | 判決規則版本化（面板編輯/歷史/恢復默認/導出）|
| GET | `/api/products` · `/api/findings` | 商品清單（依 finding）· 判決結果列表 |
| PATCH | `/api/findings/{id}/status` · `/{id}/true_label` | 單筆歸因人工覆核（確認/忽略/已修）· 標註真值分類（歸因列表操作欄用）|
| POST | `/api/auth/register`·`/login` | 帳號 |

> 完整 API：啟動後開 Swagger UI http://localhost:8100/docs

## 架構要點
- **5 來源各自獨立表**（對齊源 schema、以特徵 id 為鍵），judgments 以 `(source, source_id)` 關聯回來源表；canonical 顯示欄由 `config/ai_judge/source_mapping.json` 統一還原。
- **1:N 多歸因**：一則負向評論可判出多條獨立歸因（各自 finding_id、L1-L3、信心、判決階段），列表右側堆疊呈現、導出 fan-out。
- **判準 SSOT**＝`config/ai_judge` 規則樹（RuleManager 面板版本化，DB `judge_rule_versions` append-only 快照）。
- **配置化 SSOT**：機密 → `backend/.env`；前後端共用非機密 → `config/`（業務可調）/ `constants/`（固定字典）。
- **機密 at-rest 加密**：`user_settings` 的 provider_tokens / qc_passwords 以 Fernet 加密落庫（`app/core/crypto.py`，key＝env `AIQ_SECRET_KEY`；既有明文列遷移用 `scripts/tools/encrypt_user_secrets.py`）。`/api/settings/raw` 明文回顯僅回本人設定（JWT 守衛），屬**單機內網環境的有意識權衡**，部署公網前必須移除。

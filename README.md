# kkday-ai-quality

**AI 商品質檢平台 — AI 法官（內容爭議裁決系統）**（KKday 內容質量 Pod 第三支柱）

把客訴 / 商品差評 / 工單 / App 反饋 / 埋點等真實負面訊號，自動**歸因**到「哪個歸因域的哪個問題」（L1→L2 兩層分類），標註信心與判決階段，產出可執行方向，並反推「哪條審核規則最該優先」。目標：**降低售後進線的內容類占比**。

> 邏輯參照 [folder 2117435397](https://kkday.atlassian.net/wiki/spaces/VM/folder/2117435397)（工單驅動審核）+ [AI 法官 V2 總覽](https://kkday.atlassian.net/wiki/spaces/VM/pages/2125660181)。

## 技術棧
- **後端**：Python 3.10+ / FastAPI + SQLAlchemy Core + **PostgreSQL**（Alembic 遷移）+ OpenAI SDK（Structured Outputs）+ Pydantic
- **前端**：Vue3 + Vite + **Arco Design Vue** + Pinia + vue-echarts + vue-i18n（可替換 i18n 框架）
- **LLM**：OpenAI gpt-5 系列（無 key 時走 stub 啟發式，可零 key 走通閉環）
- **啟動 / 部署**：**Docker Compose**——一鍵 `./start.sh`（dev·hot reload）／生產 `docker compose up`（nginx 靜態 + 後端單 worker）。**macOS 只需先有 Homebrew**（start.sh 自動裝並啟動 colima 引擎），PG 與所有依賴皆在容器內。
- 文檔地圖見 [docs/README.md](./docs/README.md)（歷史選型與早期 spec 已封存於 `docs/archive/`，僅供追溯非現行契約）

## 核心流程
```
資料上傳（5 來源 xlsx/csv·自動辨識+校驗）
  → 各來源專表（product_reviews / conversations / freshdesk_tickets / app_feedback / mixpanel_tracker）
  → 初判歸因（prejudge：極性閘門 → 六域 prompt 並行判斷 → L1/L2 + 信心 + 判決階段）
  → judgments（1:N 多歸因：一則評論可判多條獨立歸因，各自一列）
  → 歸因列表 / 歸因概覽（KPI+漏斗+趨勢）/ 美化 xlsx 導出
判準來源＝prompts/*.md（Prompt-as-Source，RuleManager「初判 Prompt」md 編輯，DB append-only 版本化 + 檔案 fallback）
```

## Monorepo 結構
```
backend/                     # Python（FastAPI + SQLAlchemy + PostgreSQL）
  app/
    core/                    # 領域基礎層（見 core/README.md）
      db/                    # 資料存取（tables/source_registry + 8 職責模組 + barrel）
      judge_config/          # 6 判準 config loader（ai_judge/product_vertical/source_mapping/…）
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
start.sh · stop.sh           # 一鍵啟動 / 停止（repo 根·onboarding 入口；stop 只停止·資料一律保留）
docker-compose.yml           # 生產編排（PG + backend 單worker + frontend nginx）
docker-compose.dev.yml       # 開發編排（hot reload：source volume + uvicorn --reload + vite HMR）
docker/                      # seed/（首啟自動還原）· README.md（Docker 命令大全 + 疑難排解）
backend/Dockerfile · frontend/Dockerfile · frontend/Dockerfile.dev   # 各服務 image
```

## 🚀 啟動

### 一鍵啟動（純 Docker，推薦）
```bash
./start.sh
```
- **本機只需 Homebrew（macOS）**——start.sh 會**自動安裝並啟動容器引擎（colima·免費）→ `docker compose up`** 起全服務。無需裝 python/node/pnpm/PostgreSQL/Docker。
- 全服務在容器內：**PostgreSQL + 後端 http://localhost:8100（Swagger `/docs`）+ 前端 http://localhost:5273 + 所有依賴**。
- **就緒後自動開啟前端網頁**（http://localhost:5273；Swagger /docs 只印 URL 不開）——start.sh 背景起服務、輪詢就緒、自動 `open`。**停止**：`./stop.sh`（只停止，資料一律保留）。
- **改碼即生效**（後端 `uvicorn --reload`、前端 vite HMR，掛 source volume）；首次會 build image（較久），之後秒起。
- **schema 自動對齊**：容器啟動 entrypoint 分流——空庫 create_all+stamp、既有庫 `alembic upgrade head`；**新增 migration 後 `restart backend` 即自動套用**。

> **容器引擎全自動**：start.sh 偵測不到容器工具時會**自動安裝 [colima](https://github.com/abiosoft/colima)（免費開源，大公司免授權）並啟動**——macOS 只需先有 [Homebrew](https://brew.sh)，Linux 走 Docker 官方安裝腳本（`get.docker.com`·跨 distro）。已有 OrbStack / Docker Desktop 者直接沿用，不強制切換。真正零手動：clone → `./start.sh`。

### 常用 Docker 命令（完整命令大全 + 疑難排解見 [`docker/README.md`](./docker/README.md)）
```bash
docker compose -f docker-compose.dev.yml up -d                # 背景啟動（改依賴加 --build）
docker compose -f docker-compose.dev.yml logs -f backend      # 追 log（frontend / db 同理）
docker compose -f docker-compose.dev.yml restart backend      # 重啟單服務（新 migration 靠這個自動套用）
docker compose -f docker-compose.dev.yml exec backend bash    # 進容器 shell
docker compose -f docker-compose.dev.yml exec db psql -U postgres kkdb_ai_quality   # 直連 DB
./stop.sh                                                     # 停止（只停止·資料一律保留）
docker compose -f docker-compose.dev.yml down -v              # ⚠️ 毀滅性：連資料庫(pgdata_dev)一起清空

```
> ⚠️ **前端加套件**：主機 `pnpm add` 後容器不會自動同步，須容器內 `exec -e CI=true frontend sh -c "cd /app/frontend && pnpm install"` + `restart frontend`（SOP 見 docker/README.md）。

**載入全部數據（本地上傳，推薦）**：起來後在登入頁註冊帳號 → 「配置 › 資料導入」上傳**資料包 zip**（維護者自產：`python scripts/tools/dump_datapack.py` → `data/exports/kkday-ai-quality-datapack-*.zip`，或前台「導出資料包」下載）→ 校驗 → 輸入 `REPLACE-ALL-DATA` → 匯入。**空庫也能起**，資料靠上傳補上；或設 `SEED_URL` 由 db 首啟自動還原。

**生產部署**（後端單 worker + nginx 靜態；前端 http://localhost:8080）：
```bash
./start.sh prod   # 零配置一鍵：首次自動生成必要機密（POSTGRES_PASSWORD / AIQ_JWT_SECRET / AIQ_SECRET_KEY）
                  # 寫入 repo 根 .env（chmod 600·gitignore·冪等），之後 up -d --build；⚠️ 生成後請異地備份 .env
# 或手動注入（CI / secret manager）：三者皆必填（缺/弱即拒啟動），生成：python -c "import secrets;print(secrets.token_urlsafe(32))"
POSTGRES_PASSWORD=<pg-pass> AIQ_JWT_SECRET=<jwt-secret> AIQ_SECRET_KEY=<enc-key> docker compose up -d --build
```
> 後端目前單 worker：4 套背景 job registry（導出/導入/初判/上傳）為 in-mem，多 worker 會使 job/SSE/下載落到不同 process；job 狀態遷共享儲存（Redis/PG）後恢復多 worker。容器啟動時自動對齊 schema：**空庫** create_all + stamp head、**既有庫** `alembic upgrade head`（migration 鏈不從零跑，見 `backend/docker-entrypoint.sh`）。

**常用腳本**（見 [`scripts/README.md`](./scripts/README.md)）：`./start.sh` 一鍵 Docker 啟動 · `python scripts/tools/dump_datapack.py` 產資料包 · `./scripts/dev/dump-seed.sh` 產 seed · `./scripts/dev/lint.sh` lint · `./scripts/dev/format.sh` format。

### 進階：不使用 Docker 手動跑（選用）
> 一般用上面 Docker 一鍵即可。若要在本機直接跑，需自備 Python ≥ 3.10 / Node ≥ 20 / pnpm / 本機 PostgreSQL（`kkdb_ai_quality`）：
```bash
# 後端
cd backend && python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/uvicorn app.api.main:app --reload --port 8100
# 前端（另開終端）
cd frontend && pnpm install && cd apps/console && npx vite   # :5273，dev proxy /api → :8100
```
> schema：原生跑啟動時 `create_all` 建表並自動 stamp alembic head（空庫）；**既有庫**有新 migration 須自行 `python -m alembic upgrade head`（create_all 不改既有表；Docker dev 由 entrypoint 自動處理）。連線經 `backend/.env` `DATABASE_URL`（預設 `postgresql+psycopg2://localhost:5432/kkdb_ai_quality`）。

### LLM 模式
- **無 `OPENAI_API_KEY`**：走 **stub**（啟發式），零 key 走通整條 pipeline。
- **有 key**：面板（設定 › LLM 模型連線）或 env `OPENAI_API_KEY` 設定，自動切真 LLM。

## API 一覽（主要）
| method | path | 說明 |
|---|---|---|
| GET | `/health` | 健康檢查 |
| POST | `/api/inbound/validate`·`/upload` | 上傳乾跑校驗 → 背景落各來源專表 |
| GET | `/api/problems` | 統一問題列表（source 專表 + 歸因，伺服器端分頁，需登入）；篩選：傾向/判決階段(多選)/信心分層/覆核狀態(status 多選)/判決模型(model 多選·當前判決維度)/歸因分類(taxonomy 多選·任意層級 code 子樹語義)/日期區間/prod·order_oid |
| GET | `/api/problems/attribution_overview`·`/attribution_breakdown` | 歸因概覽聚合 + L2 下鑽；可選 model（CSV 多選）篩判決模型——當前判決維度，僅套判決級指標（total_intake 不受影響）。需登入 |
| GET | `/api/overview/ai-judge` | 質檢概覽首頁 AI 法官真實指標（內容類占比月趨勢·distinct 進線；外部指標維持示意）。需登入 |
| POST | `/api/problems/export` | 啟動問題列表 xlsx 導出背景 job（1:N 多歸因合併儲存格）→ {job_id}；`snapshot_model` 可選「輸出結果版本」＝該模型的 judgment_history 最新快照（未判過的評論排除，口徑寫入統計表附註）；`compare_models` 可選「並排對比模型」多選＝基準右側每模型附一組情緒/L1/L2 對比欄（值取該模型最新快照）|
| POST | `/api/judge-rules/export` | 啟動判決規則 xlsx 導出背景 job → {job_id} |
| GET/POST | `/api/exports/{stream,download,cancel}` | 通用導出 job：SSE 實時進度 / 取檔 / 停止（跨導出共用）|
| POST/GET | `/api/v1/judgment/prejudge/*` | 初判歸因批次（啟動/筆數預覽 count/SSE 進度/暫停/恢復/停止；目標選取可 within_ids 交集勾選範圍）。啟動/暫停/恢復/停止需 `judgment.prejudge.run` 權限；正式環境無 LLM token 拒啟動（stub 硬閘）|
| GET | `/api/v1/judgment/runs` · `/runs/{job_id}` | 歸因歷史（run 級 LLM 使用紀錄：批量/選取/單筆重判；詳情含 per-stage token/費用明細）|
| GET/POST | `/api/judgment-history` · `/notes` · `/models` | 判決歷史（**評論級**時間軸：判決快照/覆核轉移/備註三類事件；重判結果與前次全同時去重不記）· 新增評論級備註 · 歷來判決過的模型清單（篩選/導出下拉選項）。需登入 |
| CRUD | `/api/judge-rules/*` | 判決規則版本化（面板編輯/歷史/恢復默認/導出）|
| PATCH | `/api/findings/{id}/status` · `/batch/status` · `/{id}/true_label` | 單筆/批量歸因人工覆核（確認/忽略/new＝撤銷回待處理；同值冪等、轉移記入判決歷史）· 標註真值分類。需權限，記操作者/時間 audit |
| POST/GET | `/api/auth/register`·`/login`·`/me`·`/permissions` | 帳號 + 當前 user 權限清單（register 受 `AIQ_ALLOW_SELF_REGISTER` 環境閘：僅 development 預設開放）（be2 `auth.business-list` 形狀 `{value,ttl,startTime}`，供前端 v-auth/選單/守衛）|
| POST | `/api/admin/export/start` | 啟動全庫資料包導出背景 job（逐表 SSE 進度）→ {job_id}；進度/下載走通用 `/api/exports/{stream,download}`。`include_sensitive` 才含 users/user_settings。需 `data.datapack.export` 權限 |
| POST/GET | `/api/admin/import{,/validate,/stream}` | 全庫資料包安全匯入（只灌白名單表·不執行 SQL）：乾跑校驗 → 確認匯入背景 job → SSE 進度。登入即可用（qc+admin 皆有 `data.datapack.import`）+ `AIQ_ALLOW_DATA_IMPORT` 環境閘保險 |

> 完整 API：啟動後開 Swagger UI http://localhost:8100/docs

## 架構要點
- **5 來源各自獨立表**（對齊源 schema、以特徵 id 為鍵），judgments 以 `(source, source_id)` 關聯回來源表；canonical 顯示欄由 `config/ai_judge/source_mapping.json` 統一還原。
- **1:N 多歸因**：一則負向評論可判出多條獨立歸因（各自 finding_id、L1-L2、信心、判決階段），列表右側堆疊呈現、導出 fan-out。
- **判準 SSOT**＝`prompts/*.md`（Prompt-as-Source：7 支完整 prompt md，RuleManager「初判 Prompt」md 編輯 + DB `judge_rule_versions` append-only 版本化 + 檔案 fallback）；分類結構（域/L2 面向）由 `app.judge.prompt_source.structure()` 從 prompt 派生，判決引擎六域並行判斷（`prompt_pack`）。
- **配置化 SSOT**：機密 → `backend/.env`；前後端共用非機密 → `config/`（業務可調）/ `constants/`（固定字典）。
- **可替換權限框架**：後端 `PermissionProvider` 抽象 + `require_permission(key)` 守衛破壞性端點（business-key 為 be2 風格 `module.sub-function.action`；角色→key 映射 `config/global/role_permissions.json`）；前端唯一替換點 `api/permission.api.ts::fetchPermissions` → `permission.store` → `usePermission` / `v-auth` / router 守衛 / 選單過濾。換 be2 中央 Auth SVC 僅改 `auth.config.json['provider']` + `be2_provider.py` + 前端 `fetchPermissions`，其餘零改。
- **可替換 i18n 框架**：前端 `src/i18n/loader.ts::loadLocaleMessages` 為唯一翻譯來源接縫（現靜態 `locales/zh-TW/*.json`·日後接 TMS 只改此函式）；vue-i18n Composition API + `$t`。後端錯誤走 `raise_api_error(code, message)`（`DOMAIN.REASON`），前端 `errorCodeToI18nKey` 唯一轉換點對映翻譯、無對映回退中文。挖字漸進（pilot：auth login + AUTH.* error code），詳見 `frontend/apps/console/src/i18n/README.md`。
- **LLM 成本三重防線**：OpenAI prompt caching（靜態判準前綴）+ flex serving tier（批次 -50%，judgment 配置可關）+ **exact-match 結果快取**（`data/llm_cache`；重判時規則未變動部分零 token 重用，顯式單筆重判不吃快取）。
- **機密 at-rest 加密**：`user_settings` 的 provider_tokens / qc_passwords 以 Fernet 加密落庫（`app/core/crypto.py`，key＝env `AIQ_SECRET_KEY`；既有明文列遷移用 `scripts/tools/encrypt_user_secrets.py`）。**正式環境（`APP_ENV≠development`）缺 `AIQ_SECRET_KEY` 拒啟動**（避免機密明文落庫）；dev 未設則明文直通並告警。`/api/settings/raw` 明文回顯僅回本人設定（JWT 守衛），屬**單機內網環境的有意識權衡**，部署公網前必須移除。

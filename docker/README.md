# Docker 運行說明（命令大全 + 疑難排解）

全服務容器化：PostgreSQL + 後端 FastAPI + 前端。**dev / prod 兩形態分兩檔**（同服務不同 build/env/command，
不用 profiles 混一檔）；專案名固定 `kkday-ai-quality`（compose `name:`，容器/網路/volume 前綴不隨資料夾改名漂移）。

## 架構

| 服務 | dev（`docker-compose.dev.yml`） | prod（`docker-compose.yml`） |
|---|---|---|
| `db` | PG 17 · volume `pgdata_dev` · 首啟自動還原 `docker/seed/*.sql.gz` · trust 免密 | PG 17 · volume `pgdata` · **必填 `POSTGRES_PASSWORD`**（無 trust）· **不掛 seed**（防開發資料誤入正式庫；資料走 datapack 匯入或 `scripts/ops/restore-db.sh`）|
| `backend` | :8100 · uvicorn `--reload`（掛 `backend/app`+`alembic`）· APP_ENV=development | :8100(內部) · 單 worker · 嚴格 secret（缺 `AIQ_SECRET_KEY` 拒啟動）|
| `frontend` | :5273 · vite HMR（掛 `frontend/`，node_modules 走容器內匿名 volume）| :8080 · nginx 靜態 + `/api` 反代 |

兩形態共用**智慧 entrypoint**（`backend/docker-entrypoint.sh`）：**空庫**→`init_db`（create_all+stamp head）／
**既有庫**→`alembic upgrade head`——新增 migration 後**重啟容器即自動套用**，不需手動跑。

## 命令大全

### 日常（dev）

```bash
./start.sh                                        # 一鍵：自動裝引擎 → up -d → 等就緒 → 自動開前端網頁
docker compose -f docker-compose.dev.yml up -d                # 背景啟動
./stop.sh                                         # 停止（只停止·資料一律保留）
docker compose -f docker-compose.dev.yml ps                   # 看服務狀態
docker compose -f docker-compose.dev.yml logs -f backend      # 追後端 log（frontend / db 同理）
docker compose -f docker-compose.dev.yml restart backend      # 重啟單一服務（套用新 migration 就靠這個）
docker compose -f docker-compose.dev.yml exec backend bash    # 進後端容器 shell
docker compose -f docker-compose.dev.yml exec db psql -U postgres kkdb_ai_quality   # 直連資料庫
```

### 改依賴（重要 SOP）

```bash
# 前端加套件：主機 pnpm add 後，容器匿名 volume 不會自動同步 → 必須容器內補裝 + 重啟
cd frontend && pnpm --filter "./apps/console" add <pkg>
docker compose -f docker-compose.dev.yml exec -e CI=true frontend sh -c "cd /app/frontend && pnpm install"
docker compose -f docker-compose.dev.yml restart frontend

# 後端改 pyproject.toml 依賴：需重建 image
docker compose -f docker-compose.dev.yml up -d --build backend
```

### Migration / 資料

```bash
docker compose -f docker-compose.dev.yml restart backend      # 新 migration 自動套用（entrypoint 分流）
docker compose -f docker-compose.dev.yml exec backend python -m alembic current    # 看當前版本
docker compose -f docker-compose.dev.yml exec backend python -m alembic upgrade head   # 手動升（通常不需要）
./scripts/dev/dump-seed.sh                                    # 產全庫 seed（docker/seed/seed.sql.gz）
python scripts/tools/dump_datapack.py                         # 產資料包 zip（前台「資料導入」可匯入）
```

### 重建 / 清理

> **依賴層快取**：Dockerfile 已按「manifest → 裝依賴（快取層）→ code」排序——改 code 重 build **不重裝依賴**
> （實測暖快取全套重 build ~3s），只有 `pyproject.toml` / `pnpm-lock.yaml` 變動才觸發重裝；
> `.dockerignore` 已排除 `.pnpm-store`/`data/`/`docker/seed/`（context 由 ~500MB 級瘦到 KB 級）。
> 網路不穩令 `pnpm fetch` 失敗時：`--build-arg NPM_REGISTRY=https://registry.npmmirror.com` 換鏡像。

```bash
docker compose -f docker-compose.dev.yml up -d --build                # 重建 image（改 Dockerfile / 後端依賴）
docker compose -f docker-compose.dev.yml up -d --force-recreate backend   # 不重建只換容器
docker compose -f docker-compose.dev.yml rm -sfv frontend \
  && docker compose -f docker-compose.dev.yml up -d frontend           # 連匿名 volume 一起重來（前端依賴徹底重置）
docker compose -f docker-compose.dev.yml down -v                       # ⚠️ 毀滅性：刪 pgdata_dev＝資料庫全清（stop.sh 刻意不提供）
docker volume ls | grep kkday-ai-quality                               # 看本專案 volume
```

### 生產（prod）

**一鍵啟動（零配置，推薦）**：

```bash
./start.sh prod    # 首次自動生成 POSTGRES_PASSWORD / AIQ_SECRET_KEY 寫入 repo 根 .env
                   # （chmod 600·gitignore·冪等永不覆蓋既有值）→ up -d --build → 等就緒
```

> ⚠️ 生成後請**異地備份 `.env`**：`AIQ_SECRET_KEY` 遺失＝庫內已加密機密（provider token / QC 密碼）永久解不開；
> `POSTGRES_PASSWORD` 遺失＝連不上既有 `pgdata`。選填變數（`OPENAI_API_KEY` / `CORS_ALLOW_ORIGINS`）依 `.env` 內註解自行補。

**必要環境變數**（`./start.sh prod` 自動生成；改經 secret manager / CI 注入亦可，勿寫進 repo）：

| 變數 | 用途 | 缺失行為 |
|---|---|---|
| `POSTGRES_PASSWORD` | PG 密碼（取代 dev trust 免密；backend `DATABASE_URL` 同步引用） | compose 拒啟動 |
| `AIQ_SECRET_KEY` | user_settings 機密 at-rest 加密（生成 `python -c "import secrets;print(secrets.token_urlsafe(32))"`） | compose / backend 拒啟動 |
| `OPENAI_API_KEY` | LLM 初判 key（fallback；亦可登入後於設定面板配 per-provider token） | 可啟動，但 **stub 硬閘**：批量初判一律 403（防假判覆蓋真歸因） |
| `CORS_ALLOW_ORIGINS` | 前端正式網域（逗號分隔多個） | 預設 `http://localhost:8080`，上線必改 |

```bash
./start.sh prod                    # 一鍵（自動生成缺漏機密 → build → 等就緒）；前端 http://localhost:8080
POSTGRES_PASSWORD=... AIQ_SECRET_KEY=... OPENAI_API_KEY=... \
  docker compose up -d --build     # 手動注入（CI / secret manager 場景）
./stop.sh prod                     # 停止（pgdata 保留；等同 docker compose down）
docker compose logs -f backend
```

**無需 bootstrap admin / 無登入系統**：本地已廢除帳戶登錄系統（register/login/切換帳號全移除），
`authProvider=local` 下 `auth.get_current_user` 直接回固定身分（不驗 token）；權限由
`config/global/permissions.json` 的 `no_auth_grant_all=true` 全通過（單機內網環境天然無存取控制）。
若要限縮特定操作（如改 LLM 連線）給特定人，把 `no_auth_grant_all` 改 `false` 並在 `grants[email]`
授予對應 business-key（見 `backend/app/core/permissions/README.md`）。
**be2 接通後（`auth.config.json` `authProvider=be2`）**：登入走 be2 SSO，首登以 claims email 自動
provision users row；權限改由 `permissions.json` 的 `default ∪ grants[email]` 判斷（同時把
`no_auth_grant_all` 改 `false`）。
⚠️ production 設 `authProvider=be2` 需先完成 be2 token 驗簽（auth team server-to-server 契約）——未完成前後端啟動即拒（防未驗簽 token 上線）。

**TLS**：`frontend/nginx.conf` 僅 `listen 80`——由外部 LB / 反向代理 / Ingress 終止 TLS 後轉發 8080，本檔不需改。

**備份 / 還原**：`./scripts/ops/backup-db.sh`（容器化 pg_dump + 保留策略，建議 crontab 每日跑）、
`./scripts/ops/restore-db.sh <備份檔>`（破壞性，須輸入 yes 確認）。詳見 `scripts/README.md`。

**be2 權限接入（上線時）**：實作 `backend/app/core/permissions/be2_provider.py` → `config/global/auth.config.json`
`provider` 改 `"be2"` → 前端 `permission.api.ts::fetchPermissions` 換來源。僅此 3 檔，其餘零改動。

> ⚠️ dev / prod 同專案名同服務名：**image 已分 tag（`:dev`/`:prod`）、容器名已分（`-prod` 後綴·皆無 `-1` 尾碼）**，
> 但 compose 依 project+service label 識別，同一台機**仍不要同時跑兩形態**（up 會把對方容器換掉）；切換前先 down 另一邊。

## 疑難排解

| 症狀 | 原因 / 處理 |
|---|---|
| 前端 vite 報 `Failed to resolve import "<pkg>"` | 主機 `pnpm add` 後容器匿名 volume node_modules 未同步 → 跑上方「改依賴 SOP」（`CI=true` 必須，否則 pnpm 無 TTY purge 中止）|
| 後端報 `column ... does not exist` | dev DB 落後新 migration → `restart backend`（entrypoint 自動 `alembic upgrade head`）|
| 容器都 `Up` 但瀏覽器連不上（`curl localhost:PORT`=000）| colima host↔VM 轉發橋失效：`colima stop && colima start`（**`colima restart` 不夠**）再 `up -d` |
| port 5273/8100 被佔 | `./start.sh` 已**自動避讓**（偵測占用→改鄰近空閒端口，並印實際 URL；本專案容器已在跑則沿用）。要強制用某端口：`FRONTEND_PORT=5999 ./start.sh`。要釋放預設端口給本專案：`lsof -nP -iTCP:8100 -sTCP:LISTEN` → kill 原生 |
| 後端行為停在舊 code（reload 卡死）| `docker compose -f docker-compose.dev.yml restart backend`；原生跑則 `lsof -ti:8100 \| xargs kill -9` |
| 匯入資料包報 schema 版本不符 | 資料包是舊 schema 導出的 → 重新「導出資料包」再匯入（或該環境先 `restart backend` 升 schema）|
| build 很慢 / 想看 build log | `docker compose -f docker-compose.dev.yml build --progress=plain backend` |
| 想全部砍掉重來 | `docker compose -f docker-compose.dev.yml down -v` → `./start.sh`（⚠️ 刪庫；資料靠 seed 或前台資料包匯入）|

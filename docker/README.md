# Docker 運行說明（命令大全 + 疑難排解）

全服務容器化：PostgreSQL + 後端 FastAPI + 前端。**dev / prod 兩形態分兩檔**（同服務不同 build/env/command，
不用 profiles 混一檔）；專案名固定 `kkday-ai-quality`（compose `name:`，容器/網路/volume 前綴不隨資料夾改名漂移）。

## 架構

| 服務 | dev（`docker-compose.dev.yml`） | prod（`docker-compose.yml`） |
|---|---|---|
| `db` | PG 17 · volume `pgdata_dev` · 首啟自動還原 `docker/seed/*.sql.gz` | PG 17 · volume `pgdata` · 同 seed 機制 |
| `backend` | :8100 · uvicorn `--reload`（掛 `backend/app`+`alembic`）· APP_ENV=development | :8100(內部) · 單 worker · 嚴格 secret（缺 `AIQ_JWT_SECRET`/`AIQ_SECRET_KEY` 拒啟動）|
| `frontend` | :5273 · vite HMR（掛 `frontend/`，node_modules 走容器內匿名 volume）| :8080 · nginx 靜態 + `/api` 反代 |

兩形態共用**智慧 entrypoint**（`backend/docker-entrypoint.sh`）：**空庫**→`init_db`（create_all+stamp head）／
**既有庫**→`alembic upgrade head`——新增 migration 後**重啟容器即自動套用**，不需手動跑。

## 命令大全

### 日常（dev）

```bash
./start.sh                                        # 一鍵：自動裝引擎（colima）→ up（前景，Ctrl-C 停）
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

```bash
# 兩 secret 必填且 ≥32 bytes（缺/弱拒啟動）；生成：python -c "import secrets;print(secrets.token_urlsafe(32))"
AIQ_JWT_SECRET=... AIQ_SECRET_KEY=... docker compose up -d --build     # 前端 http://localhost:8080
docker compose down                                                    # 停止（pgdata 保留）
docker compose logs -f backend
```

> ⚠️ dev / prod 同專案名同服務名：同一台機**不要同時跑兩形態**（compose 會把對方容器換掉）；切換前先 down 另一邊。

## 疑難排解

| 症狀 | 原因 / 處理 |
|---|---|
| 前端 vite 報 `Failed to resolve import "<pkg>"` | 主機 `pnpm add` 後容器匿名 volume node_modules 未同步 → 跑上方「改依賴 SOP」（`CI=true` 必須，否則 pnpm 無 TTY purge 中止）|
| 後端報 `column ... does not exist` | dev DB 落後新 migration → `restart backend`（entrypoint 自動 `alembic upgrade head`）|
| 容器都 `Up` 但瀏覽器連不上（`curl localhost:PORT`=000）| colima host↔VM 轉發橋失效：`colima stop && colima start`（**`colima restart` 不夠**）再 `up -d` |
| port 5273/8100 被佔 | 本機原生 uvicorn/vite 還在跑：`lsof -nP -iTCP:8100 -sTCP:LISTEN`（`ssh`=容器轉發、`Python`=原生）→ kill 原生 |
| 後端行為停在舊 code（reload 卡死）| `docker compose -f docker-compose.dev.yml restart backend`；原生跑則 `lsof -ti:8100 \| xargs kill -9` |
| 匯入資料包報 schema 版本不符 | 資料包是舊 schema 導出的 → 重新「導出資料包」再匯入（或該環境先 `restart backend` 升 schema）|
| build 很慢 / 想看 build log | `docker compose -f docker-compose.dev.yml build --progress=plain backend` |
| 想全部砍掉重來 | `docker compose -f docker-compose.dev.yml down -v` → `./start.sh`（⚠️ 刪庫；資料靠 seed 或前台資料包匯入）|

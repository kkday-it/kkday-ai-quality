#!/usr/bin/env bash
# 一鍵啟動（純 Docker）：偵測 Docker → 未啟動則啟動並等待 → docker compose up（dev·hot reload）。
#   ./scripts/dev/start.sh
# 全服務在容器內：PostgreSQL + 後端 :8100 + 前端 :5273 + 所有依賴。本機只需 Docker，無需裝 python/node/pnpm/PG。
# 改碼即生效（uvicorn --reload + vite HMR）；首次會 build image（較久），之後秒起。Ctrl-C 停止所有服務。
# 資料：空庫也能起 → 於前端「配置 › 資料導入」上傳資料包載入；或先設 SEED_URL 由 db 首啟自動還原。
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

# 1. Docker CLI 存在？
if ! command -v docker >/dev/null 2>&1; then
  echo "❌ 未偵測到 Docker。請先安裝 Docker Desktop → https://www.docker.com/products/docker-desktop/"
  exit 1
fi

# 2. Docker daemon 已啟動？未啟動則嘗試啟動並等待就緒（最長 ~2 分鐘）
if ! docker info >/dev/null 2>&1; then
  echo "🐳 Docker 未啟動，嘗試啟動 ..."
  case "$(uname -s)" in
    Darwin)
      # 依序試常見引擎：OrbStack → Docker Desktop → colima
      if [ -d "/Applications/OrbStack.app" ]; then
        open -a OrbStack >/dev/null 2>&1 || true
      elif [ -d "/Applications/Docker.app" ]; then
        open -a Docker >/dev/null 2>&1 || true
      elif command -v colima >/dev/null 2>&1; then
        colima start >/dev/null 2>&1 || true
      else
        echo "⚠️ 找不到 Docker 引擎（OrbStack / Docker Desktop / colima），請手動啟動後重試"
      fi
      ;;
    Linux) sudo systemctl start docker 2>/dev/null || sudo service docker start 2>/dev/null || true ;;
    *) echo "⚠️ 未知平台，請手動啟動 Docker 後重試" ;;
  esac
  printf "   等待 Docker 就緒"
  for i in $(seq 1 60); do
    if docker info >/dev/null 2>&1; then
      printf " ✓\n"
      break
    fi
    printf "."
    sleep 2
    if [ "$i" = 60 ]; then
      printf "\n❌ Docker 啟動逾時，請手動開啟 Docker Desktop 後重試\n"
      exit 1
    fi
  done
fi

# 3.（選用）取得 seed 供 db 首啟自動還原；無 SEED_URL / 本地檔則略過（空庫，改前台導入資料包）
"$ROOT/scripts/dev/fetch-seed.sh" >/dev/null 2>&1 || true

# 4. 起全部服務（dev·hot reload）；Ctrl-C 停止。首次自動 build image。
echo "🚀 啟動所有服務（Docker · hot reload）..."
echo "   後端 http://localhost:8100（Swagger /docs）｜前端 http://localhost:5273"
echo "   首次會 build image，請稍候；之後秒起。（改依賴時加 --build 重建）"
exec docker compose -f docker-compose.dev.yml up

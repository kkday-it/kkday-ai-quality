#!/usr/bin/env bash
# 一鍵啟動（純 Docker）：自動裝並啟動容器引擎（優先 colima 免費開源）→ docker compose up（dev·hot reload）。
#   ./start.sh
# 全服務在容器內：PostgreSQL + 後端 :8100 + 前端 :5273 + 所有依賴。本機只需 Homebrew（macOS）；其餘 start.sh 自動裝。
# 改碼即生效（uvicorn --reload + vite HMR）；首次會 build image（較久），之後秒起。Ctrl-C 停止所有服務。
# 資料：空庫也能起 → 於前端「配置 › 資料導入」上傳資料包載入；或先設 SEED_URL 由 db 首啟自動還原。
set -uo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"  # 本檔在 repo 根（與 docker-compose.dev.yml 同級）
cd "$ROOT"

# 1. 容器工具（docker CLI）存在？缺則自動安裝 colima（免費開源·大公司免授權）
if ! command -v docker >/dev/null 2>&1; then
  echo "📦 未偵測到容器工具，自動安裝 colima（免費開源）..."
  case "$(uname -s)" in
    Darwin)
      command -v brew >/dev/null 2>&1 || {
        echo "❌ 需先安裝 Homebrew（https://brew.sh）再重跑 start.sh"
        exit 1
      }
      brew install colima docker docker-compose || { echo "❌ colima 安裝失敗"; exit 1; }
      ;;
    Linux)
      # Docker 官方安裝腳本（跨 distro·含 compose plugin；比 distro 套件穩定，避開 docker-compose-plugin 各版本包名不一）
      curl -fsSL https://get.docker.com | sudo sh || { echo "❌ Docker 安裝失敗，請手動安裝"; exit 1; }
      sudo systemctl enable --now docker 2>/dev/null || sudo service docker start 2>/dev/null || true
      ;;
    *) echo "❌ 未知平台，請手動安裝容器引擎"; exit 1 ;;
  esac
fi

# 2. Docker daemon 已啟動？未啟動則嘗試啟動並等待就緒（最長 ~2 分鐘）
if ! docker info >/dev/null 2>&1; then
  echo "🐳 容器引擎未啟動，嘗試啟動 ..."
  case "$(uname -s)" in
    Darwin)
      # 優先 colima（免費開源·大公司免授權）；無則退 OrbStack / Docker Desktop
      if command -v colima >/dev/null 2>&1; then
        colima start >/dev/null 2>&1 || true
      elif [ -d "/Applications/OrbStack.app" ]; then
        open -a OrbStack >/dev/null 2>&1 || true
      elif [ -d "/Applications/Docker.app" ]; then
        open -a Docker >/dev/null 2>&1 || true
      elif command -v brew >/dev/null 2>&1; then
        echo "📦 無容器引擎，自動安裝 colima（免費開源）..."
        brew install colima docker docker-compose && colima start >/dev/null 2>&1 || true
      else
        echo "⚠️ 無容器引擎且無 Homebrew，請先裝 brew（https://brew.sh）後重跑"
      fi
      ;;
    Linux) sudo systemctl start docker 2>/dev/null || sudo service docker start 2>/dev/null || true ;;
    *) echo "⚠️ 未知平台，請手動啟動容器引擎後重試" ;;
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
      printf "\n❌ 容器引擎啟動逾時。請手動啟動（colima start ｜ 或開 OrbStack/Docker Desktop）後重試\n"
      exit 1
    fi
  done
fi

# 3.（選用）取得 seed 供 db 首啟自動還原；無 SEED_URL / 本地檔則略過（空庫，改前台導入資料包）
"$ROOT/scripts/dev/fetch-seed.sh" >/dev/null 2>&1 || true

# 4. 背景起全部服務（dev·hot reload）；首次自動 build image（依賴層有快取，之後秒起）。
echo "🚀 啟動所有服務（Docker · hot reload）..."
echo "   首次會 build image，請稍候；之後秒起。（改依賴時加 --build 重建）"
docker compose -f docker-compose.dev.yml up -d || { echo "❌ 啟動失敗；詳見 docker compose -f docker-compose.dev.yml logs"; exit 1; }

FRONTEND_URL="http://localhost:5273"
BACKEND_DOCS_URL="http://localhost:8100/docs"

# 5. 等前後端就緒（探 HTTP 可回應即可；後端含 entrypoint schema 對齊時間）
_probe() { # $1=url → 可連且回 HTTP 即 0（不要求 200：/docs 重導、vite 對無 Accept 回 404 皆算活）
  command curl -s -o /dev/null --max-time 2 "$1" 2>/dev/null
}
printf "   等待服務就緒"
for i in $(seq 1 90); do
  if _probe "http://localhost:8100/health" && _probe "$FRONTEND_URL"; then
    printf " ✓\n"
    break
  fi
  printf "."
  sleep 2
  if [ "$i" = 90 ]; then
    printf "\n⚠️ 服務未在 3 分鐘內就緒；查 log：docker compose -f docker-compose.dev.yml logs -f\n"
    exit 1
  fi
done

# 6. 自動打開前端網頁（macOS open / Linux xdg-open；無圖形環境則只印 URL）。Swagger 只印不開。
_open() {
  if command -v open >/dev/null 2>&1; then open "$1"
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$1" >/dev/null 2>&1 || true
  fi
}
_open "$FRONTEND_URL"

echo "✅ 全部就緒（已在瀏覽器開啟前端）"
# ⚠️ 變數緊貼全形字必用 ${VAR}：8-bit locale（如 ISO8859-1 終端）下 bash 會把全形字首位元組
# 當字母吃進變數名 → unbound variable（實測踩到）；大括號在任何 locale 都明確終止變數名。
echo "   前端  ${FRONTEND_URL}"
echo "   後端  ${BACKEND_DOCS_URL}（Swagger）"
echo "   log： docker compose -f docker-compose.dev.yml logs -f backend"
echo "   停止：./stop.sh（資料保留）"

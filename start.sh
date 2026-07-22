#!/usr/bin/env bash
# 一鍵啟動（純 Docker）：自動裝並啟動容器引擎（優先 colima 免費開源）→ docker compose up。
#   ./start.sh          # dev（預設）：hot reload；前端 :5273（被占用自動改鄰近空閒端口·可 FRONTEND_PORT=X 指定）
#   ./start.sh prod     # prod（零配置）：首次自動生成必要機密寫入 repo 根 .env（冪等），前端 :8080
# 全服務在容器內：PostgreSQL + 後端 + 前端 + 所有依賴。本機只需 Homebrew（macOS）；其餘 start.sh 自動裝。
# dev：改碼即生效；空庫可於前端「配置 › 資料導入」上傳資料包，或先設 SEED_URL 由 db 首啟自動還原。
# prod：⚠️ 首次生成後請異地備份 .env——AIQ_SECRET_KEY 遺失＝庫內已加密機密（provider token / QC 密碼）不可復原。
# 首次會 build image（較久），之後秒起。停止：./stop.sh（dev）／ ./stop.sh prod。
set -uo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"  # 本檔在 repo 根（與兩份 compose 檔同級）
cd "$ROOT"

# 0. 模式分流（dev 預設；其餘參數拒絕，防打錯字被靜默當 dev 跑）
MODE="${1:-dev}"
if [ "$MODE" != "dev" ] && [ "$MODE" != "prod" ]; then
  echo "❌ 用法：./start.sh [prod]（不帶參數＝dev）"
  exit 1
fi
if [ "$MODE" = "prod" ]; then
  COMPOSE_FILE="docker-compose.yml"
  FRONTEND_URL="http://localhost:8080"
  OTHER_MODE="dev"
  OTHER_CONTAINERS='^kkday-ai-quality-(db|backend|frontend)$'  # dev 容器（無 -prod 尾碼）
  OTHER_STOP_HINT="./stop.sh"
else
  COMPOSE_FILE="docker-compose.dev.yml"
  FRONTEND_URL="http://localhost:5273"
  OTHER_MODE="prod"
  OTHER_CONTAINERS='^kkday-ai-quality-(db|backend|frontend)-prod$'
  OTHER_STOP_HINT="./stop.sh prod"
fi

# 動態端口（dev）：預設 5273/8100 被占用時自動改鄰近空閒端口，避免與其他本地專案衝突。
# 可 FRONTEND_PORT=5999 ./start.sh 指定偏好（仍會避讓占用）；compose 讀 ${FRONTEND_PORT}/${BACKEND_PORT}。
_port_free() {  # $1=port → 0＝空閒（無人 listen）／非 0＝占用
  if command -v lsof >/dev/null 2>&1; then
    ! lsof -iTCP:"$1" -sTCP:LISTEN -n -P >/dev/null 2>&1
  else
    ! (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null  # bash /dev/tcp：連得上＝占用
  fi
}
_pick_port() {  # $1=偏好 port → 印第一個空閒（偏好起，最多 +50）；全占用回偏好讓 docker 報明確錯
  local p="$1" n=0
  while [ "$n" -lt 50 ]; do
    _port_free "$p" && { echo "$p"; return 0; }
    p=$((p + 1)); n=$((n + 1))
  done
  echo "$1"
}

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

# 3. 兩形態互斥閘：compose 依 project+service 識別，up 會把對方容器換掉（見 docker/README.md）
if docker ps --format '{{.Names}}' | grep -qE "$OTHER_CONTAINERS"; then
  echo "❌ 偵測到 ${OTHER_MODE} 形態容器運行中；同機兩形態互斥（up 會互換容器）。"
  echo "   請先停止另一邊：${OTHER_STOP_HINT}"
  exit 1
fi

if [ "$MODE" = "prod" ]; then
  # 4p. 機密 .env 引導（冪等：每個 key 已有非空值即跳過，永不重新生成——
  #     重生 AIQ_SECRET_KEY 會令庫內密文永久解不開、重生 POSTGRES_PASSWORD 會連不上既有 pgdata）
  _gen_hex() { # $1=bytes → hex 字串（僅 [0-9a-f]：URL / compose 變數替換 / sed 皆安全）
    if command -v openssl >/dev/null 2>&1; then
      openssl rand -hex "$1"
    else
      od -An -N"$1" -tx1 /dev/urandom | tr -d ' \n'
    fi
  }
  _ensure_secret() { # $1=KEY $2=bytes：.env 無該 key → 追加；有 key 但空值 → 原地補值；已有值 → 不動
    local key="$1" bytes="$2" cur val
    cur="$(grep -E "^${key}=" .env | tail -n1 | cut -d= -f2- || true)"
    [ -n "$cur" ] && return 0
    val="$(_gen_hex "$bytes")"
    if [ -z "$val" ]; then
      echo "❌ 無法生成隨機值（缺 openssl 且 /dev/urandom 不可讀）"
      exit 1
    fi
    if grep -qE "^${key}=" .env; then
      sed -i.bak "s|^${key}=$|${key}=${val}|" .env && rm -f .env.bak
    else
      [ -n "$(tail -c1 .env)" ] && echo >> .env  # 檔尾無換行則補（防串接到末行）
      printf '%s=%s\n' "$key" "$val" >> .env
    fi
    echo "   🔑 已生成 ${key} → .env"
  }
  if [ ! -f .env ]; then
    echo "🔐 首次生產啟動：自動生成機密（寫入 ${ROOT}/.env）"
    (umask 077; cat > .env <<'EOF'
# 生產機密（./start.sh prod 首次自動生成；gitignore，勿提交）。
# ⚠️ 請異地備份本檔：AIQ_SECRET_KEY 遺失＝庫內已加密機密永久解不開。
# 選填 OPENAI_API_KEY=（LLM fallback token；一般改由前端「設定」面板配置，加密落庫）
# 選填 CORS_ALLOW_ORIGINS=（前端正式網域，逗號分隔；預設 http://localhost:8080，上線必改）
EOF
    )
  fi
  chmod 600 .env
  _ensure_secret POSTGRES_PASSWORD 24
  _ensure_secret AIQ_SECRET_KEY 32

  # 5p. 啟動（prod 一律 --build：依賴層有快取，改 code 重 build 秒級）
  echo "🚀 啟動生產服務（${COMPOSE_FILE}）..."
  docker compose -f "$COMPOSE_FILE" up -d --build || { echo "❌ 啟動失敗；詳見 docker compose logs"; exit 1; }
else
  # 4d0. 動態端口：本專案容器已在跑 → 沿用現有 host 端口（冪等重跑，勿把自己占的端口誤判為衝突而跳號）；
  #       否則預設 5273/8100 被占即自動改鄰近空閒端口。export 供 compose 的 ${FRONTEND_PORT}/${BACKEND_PORT}。
  _cur_port() {  # $1=容器名 → 印其任一 published host port（未跑/無映射則空）
    docker inspect "$1" --format '{{range $c := .NetworkSettings.Ports}}{{if $c}}{{(index $c 0).HostPort}}{{"\n"}}{{end}}{{end}}' 2>/dev/null | head -n1
  }
  _fe_cur="$(_cur_port kkday-ai-quality-frontend)"
  if [ -n "$_fe_cur" ]; then
    FRONTEND_PORT="$_fe_cur"
    BACKEND_PORT="$(_cur_port kkday-ai-quality-backend)"; BACKEND_PORT="${BACKEND_PORT:-8100}"
    echo "ℹ️ 本專案容器已在跑，沿用端口：前端 ${FRONTEND_PORT} / 後端 ${BACKEND_PORT}"
  else
    FRONTEND_PORT="$(_pick_port "${FRONTEND_PORT:-5273}")"
    BACKEND_PORT="$(_pick_port "${BACKEND_PORT:-8100}")"
    [ "$FRONTEND_PORT" != "5273" ] && echo "ℹ️ 前端 5273 被占用 → 改用 ${FRONTEND_PORT}"
    [ "$BACKEND_PORT" != "8100" ] && echo "ℹ️ 後端 8100 被占用 → 改用 ${BACKEND_PORT}"
  fi
  export FRONTEND_PORT BACKEND_PORT
  FRONTEND_URL="http://localhost:${FRONTEND_PORT}"

  # 4d.（選用）取得 seed 供 db 首啟自動還原；無 SEED_URL / 本地檔則略過（空庫，改前台導入資料包）
  "$ROOT/scripts/dev/fetch-seed.sh" >/dev/null 2>&1 || true

  # 5d. 背景起全部服務（dev·hot reload）；首次自動 build image（依賴層有快取，之後秒起）。
  echo "🚀 啟動所有服務（Docker · hot reload）..."
  echo "   首次會 build image，請稍候；之後秒起。（改依賴時加 --build 重建）"
  docker compose -f "$COMPOSE_FILE" up -d || { echo "❌ 啟動失敗；詳見 docker compose -f ${COMPOSE_FILE} logs"; exit 1; }
fi

BACKEND_DOCS_URL="http://localhost:${BACKEND_PORT:-8100}/docs"

# 6. 等服務就緒。dev：直探前後端 HTTP；prod：backend :8100 僅容器內 expose，改讀容器 healthcheck。
_probe() { # $1=url → 可連且回 HTTP 即 0（不要求 200：/docs 重導、vite 對無 Accept 回 404 皆算活）
  command curl -s -o /dev/null --max-time 2 "$1" 2>/dev/null
}
_backend_ready() {
  if [ "$MODE" = "prod" ]; then
    [ "$(docker inspect -f '{{.State.Health.Status}}' kkday-ai-quality-backend-prod 2>/dev/null || echo starting)" = "healthy" ]
  else
    _probe "http://localhost:${BACKEND_PORT:-8100}/api/status"
  fi
}
printf "   等待服務就緒"
for i in $(seq 1 90); do
  if _backend_ready && _probe "$FRONTEND_URL"; then
    printf " ✓\n"
    break
  fi
  printf "."
  sleep 2
  if [ "$i" = 90 ]; then
    printf "\n⚠️ 服務未在 3 分鐘內就緒；查 log：docker compose -f %s logs -f backend\n" "$COMPOSE_FILE"
    exit 1
  fi
done

# 7. 自動打開前端網頁（macOS open / Linux xdg-open；無圖形環境則只印 URL）。Swagger 只印不開。
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
if [ "$MODE" = "prod" ]; then
  echo "   機密  ${ROOT}/.env（⚠️ 請異地備份；AIQ_SECRET_KEY 遺失＝加密資料不可復原）"
  echo "   首次部署請建 admin 帳號（bootstrap 流程見 docker/README.md「生產」節）"
  echo "   log： docker compose logs -f backend"
  echo "   停止：./stop.sh prod（資料保留）"
else
  # ⚠️（Swagger）必須放在 URL 前：全形括號緊貼 URL 尾端會被終端的連結偵測一起吃進去，
  # 點擊變成 http://…/docs%EF%BC%88Swagger%EF%BC%89（實測踩到）。
  echo "   後端（Swagger）  ${BACKEND_DOCS_URL}"
  echo "   log： docker compose -f docker-compose.dev.yml logs -f backend"
  echo "   停止：./stop.sh（資料保留）"
fi

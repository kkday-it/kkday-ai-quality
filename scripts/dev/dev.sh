#!/usr/bin/env bash
# 一鍵啟動前後端（Ctrl-C 同時停）。
#   ./scripts/dev/dev.sh
# 後端：backend/run.sh（venv + 依賴 + uvicorn，port 8100，Swagger /docs）
# 前端：frontend pnpm dev（vite，本機 port 5273）
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"   # scripts/dev/ 的上兩層＝repo 根
cd "$ROOT"

# 前置自檢閘門：工具鏈 / 依賴 / .env / DB / migration，冪等修復；不過關即中止，不帶病啟動
"$ROOT/scripts/dev/doctor.sh" || { echo "❌ 環境自檢未通過，請依上方指令修復後重試"; exit 1; }

# 後端背景啟動（run.sh 會自建 venv / 裝依賴 / 跑 uvicorn --reload）
( cd backend && ./run.sh ) &
BACK_PID=$!

# 結束 / Ctrl-C 時連帶收掉後端（含 uvicorn reload 子程序）
cleanup() {
  echo ""
  echo "🛑 停止後端 (pid $BACK_PID) ..."
  kill "$BACK_PID" 2>/dev/null || true
  pkill -P "$BACK_PID" 2>/dev/null || true
  pkill -f "uvicorn app.api.main:app" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "🚀 後端：http://localhost:8100  (Swagger: http://localhost:8100/docs)"
echo "🎨 前端啟動中（vite，埠見下方輸出，本機通常 http://localhost:5273）..."
echo ""

# 前端前景啟動；它停下（Ctrl-C）→ 觸發 trap 收後端
cd frontend && pnpm dev

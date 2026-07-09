#!/usr/bin/env bash
# 一鍵啟動前後端（首次含 bootstrap：系統工具 / 依賴 / DB / seed；Ctrl-C 同時停）。
#   ./scripts/dev/start.sh
# 後端：backend/run.sh（venv + 依賴 + uvicorn，port 8100，Swagger /docs）
# 前端：frontend pnpm dev（vite，本機 port 5273）
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"   # scripts/dev/ 的上兩層＝repo 根
cd "$ROOT"

# ── 0. Bootstrap：系統級工具偵測 + 協助安裝（缺才裝，裝前確認）──────────────
#     doctor.sh 維持 report-only 檢查閘門；安裝動作集中於此 lib（見檔內設計說明）。
# shellcheck source=scripts/dev/lib/ensure-system-tools.sh
. "$ROOT/scripts/dev/lib/ensure-system-tools.sh"
ensure_system_tools || { echo "❌ 系統工具未就緒，請依上方指令處理後重試"; exit 1; }

# ── 0b. Seed：本機 DB 為空且有 seed 來源時，還原你的全部數據（首次 onboarding）──
#      無 seed 來源 → 非致命略過，以空庫啟動（backend create_all 建空 schema）。
"$ROOT/scripts/dev/fetch-seed.sh" --restore-if-empty || true

# ── 1. 前置自檢閘門：工具鏈 / 依賴 / .env / DB / migration，冪等修復；不過關即中止 ──
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

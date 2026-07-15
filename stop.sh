#!/usr/bin/env bash
# 停止一鍵啟動的所有服務（與 start.sh 成對）。只做停止——資料 volume 一律保留。
#   ./stop.sh           # 停 dev
#   ./stop.sh prod      # 停 prod
# 要清空資料庫（毀滅性）不在本腳本：請顯式跑
#   docker compose -f docker-compose.dev.yml down -v   # ⚠️ 連 pgdata_dev 一起刪 → 全新空庫
set -uo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"  # 本檔在 repo 根（與兩份 compose 檔同級）
cd "$ROOT"

# 僅接受模式參數（dev 預設 / prod）：停止腳本不承載毀滅性行為（曾有 --wipe flag，因易誤觸移除）
MODE="${1:-dev}"
if [ "$#" -gt 1 ] || { [ "$MODE" != "dev" ] && [ "$MODE" != "prod" ]; }; then
  echo "❌ 用法：./stop.sh [prod]（不帶參數＝停 dev）；只做停止（資料保留）。"
  echo "   要清空資料庫請顯式跑：docker compose -f docker-compose.dev.yml down -v"
  exit 1
fi
COMPOSE_FILE="docker-compose.dev.yml"
[ "$MODE" = "prod" ] && COMPOSE_FILE="docker-compose.yml"

# Docker 沒裝 / 沒跑 → 沒東西可停，直接結束
if ! command -v docker >/dev/null 2>&1 || ! docker info >/dev/null 2>&1; then
  echo "ℹ️ 容器引擎未運行，無需停止。"
  exit 0
fi

echo "🛑 停止所有服務（${MODE}·保留資料 volume）..."
docker compose -f "$COMPOSE_FILE" down
if [ "$MODE" = "prod" ]; then
  echo "✓ 已停止（資料保留）。重啟：./start.sh prod"
else
  echo "✓ 已停止（資料保留）。重啟：./start.sh"
fi

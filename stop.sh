#!/usr/bin/env bash
# 停止一鍵啟動的所有服務（與 start.sh 成對）。只做停止——資料 volume 一律保留。
#   ./stop.sh
# 要清空資料庫（毀滅性）不在本腳本：請顯式跑
#   docker compose -f docker-compose.dev.yml down -v   # ⚠️ 連 pgdata_dev 一起刪 → 全新空庫
set -uo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"  # 本檔在 repo 根（與 docker-compose.dev.yml 同級）
cd "$ROOT"

# 拒絕任何參數：停止腳本不承載毀滅性行為（曾有 --wipe flag，因易誤觸移除）
if [ "$#" -gt 0 ]; then
  echo "❌ stop.sh 不接受參數，只做停止（資料保留）。"
  echo "   要清空資料庫請顯式跑：docker compose -f docker-compose.dev.yml down -v"
  exit 1
fi

# Docker 沒裝 / 沒跑 → 沒東西可停，直接結束
if ! command -v docker >/dev/null 2>&1 || ! docker info >/dev/null 2>&1; then
  echo "ℹ️ 容器引擎未運行，無需停止。"
  exit 0
fi

echo "🛑 停止所有服務（保留資料 volume）..."
docker compose -f docker-compose.dev.yml down
echo "✓ 已停止（資料保留）。重啟：./start.sh"

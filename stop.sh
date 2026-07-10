#!/usr/bin/env bash
# 停止一鍵啟動的所有服務（與 start.sh 成對）。
#   ./stop.sh          # 停止並移除容器/網路，保留資料 volume（重跑 start.sh 資料還在）
#   ./stop.sh --wipe   # 連資料 volume(pgdata_dev) 一起刪 → 全新空庫重來
set -uo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"  # 本檔在 repo 根（與 docker-compose.dev.yml 同級）
cd "$ROOT"

# Docker 沒裝 / 沒跑 → 沒東西可停，直接結束
if ! command -v docker >/dev/null 2>&1 || ! docker info >/dev/null 2>&1; then
  echo "ℹ️ 容器引擎未運行，無需停止。"
  exit 0
fi

if [ "${1:-}" = "--wipe" ]; then
  echo "🧹 停止並清空資料（含 pgdata_dev volume）..."
  docker compose -f docker-compose.dev.yml down -v
  echo "✓ 已停止並清空資料。重啟（全新空庫）：./start.sh"
else
  echo "🛑 停止所有服務（保留資料 volume）..."
  docker compose -f docker-compose.dev.yml down
  echo "✓ 已停止（資料保留）。重啟：./start.sh"
fi

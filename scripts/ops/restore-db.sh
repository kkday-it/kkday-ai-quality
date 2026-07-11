#!/usr/bin/env bash
# 還原 backup-db.sh 產出的備份（**破壞性**：備份帶 --clean --if-exists，灌入前先 DROP 既有物件）。
#
# 用法：
#   ./scripts/ops/restore-db.sh backups/db/kkdb_ai_quality_20260711_030000.sql.gz
#   COMPOSE_FILE=docker-compose.dev.yml ./scripts/ops/restore-db.sh <file>   # 還原到 dev 庫
#
# 還原後建議 `docker compose restart backend`：entrypoint 會 alembic upgrade head，
# 讓備份時較舊的 schema 自動補齊到當前版本。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

FILE="${1:?用法：./scripts/ops/restore-db.sh <備份檔路徑>}"
[ -f "$FILE" ] || { echo "❌ 檔案不存在：$FILE" >&2; exit 1; }

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"
DB_SERVICE="db"
DB_NAME="kkdb_ai_quality"

# 破壞性操作二次確認（type-to-confirm；非互動環境請勿接自動化管線）
read -r -p "⚠️  將 DROP 並覆蓋 ${DB_NAME}（compose: ${COMPOSE_FILE}）。輸入 yes 繼續： " CONFIRM
[ "$CONFIRM" = "yes" ] || { echo "已取消"; exit 1; }

echo "▶ 還原 ${FILE} → ${DB_NAME} ..."
gunzip -c "$FILE" | docker compose -f "$COMPOSE_FILE" exec -T "$DB_SERVICE" \
  psql -U postgres -d "$DB_NAME" -q
echo "✓ 還原完成；建議接著：docker compose -f ${COMPOSE_FILE} restart backend（自動 alembic upgrade head）"

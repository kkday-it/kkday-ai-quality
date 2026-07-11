#!/usr/bin/env bash
# 生產資料庫備份（容器化 pg_dump：docker compose exec 於 db 容器內執行，免本機裝 PG client）。
#
# 用法：
#   ./scripts/ops/backup-db.sh                       # → backups/db/kkdb_ai_quality_YYYYmmdd_HHMMSS.sql.gz
#   ./scripts/ops/backup-db.sh --keep 14             # 保留最近 14 份（預設 7，超出自動清舊）
#   COMPOSE_FILE=docker-compose.dev.yml ./scripts/ops/backup-db.sh   # 備份 dev 庫（預設 prod compose）
#
# 產物特性（與 scripts/dev/dump-seed.sh 同參數語義）：
#   - --clean --if-exists：restore-db.sh 還原時先 DROP 再建，可重複灌入
#   - --no-owner --no-privileges：跨機還原免 role/權限問題
#
# 排程建議（crontab -e）：
#   0 3 * * * cd /path/to/kkday-ai-quality && ./scripts/ops/backup-db.sh --keep 14 >> backups/db/backup.log 2>&1
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"
DB_SERVICE="db"
DB_NAME="kkdb_ai_quality"
OUT_DIR="backups/db"
KEEP=7
[ "${1:-}" = "--keep" ] && KEEP="${2:?--keep 需帶份數}"

mkdir -p "$OUT_DIR"
TS="$(date +%Y%m%d_%H%M%S)"
OUT="${OUT_DIR}/${DB_NAME}_${TS}.sql.gz"

echo "▶ docker compose exec ${DB_SERVICE} pg_dump ${DB_NAME} → ${OUT} ..."
docker compose -f "$COMPOSE_FILE" exec -T "$DB_SERVICE" \
  pg_dump --no-owner --no-privileges --clean --if-exists -U postgres "$DB_NAME" \
  | gzip -9 > "$OUT"
echo "✓ 完成：${OUT}（$(du -h "$OUT" | cut -f1)）"

# 保留策略：只留最近 $KEEP 份（依 mtime 新→舊排序，砍尾）
COUNT="$(ls -1 "$OUT_DIR"/${DB_NAME}_*.sql.gz 2>/dev/null | wc -l | tr -d ' ')"
if [ "$COUNT" -gt "$KEEP" ]; then
  ls -1t "$OUT_DIR"/${DB_NAME}_*.sql.gz | tail -n +"$((KEEP + 1))" | xargs rm -f
  echo "→ 已清理舊備份，保留最近 ${KEEP} 份"
fi

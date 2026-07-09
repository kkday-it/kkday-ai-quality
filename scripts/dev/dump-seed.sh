#!/usr/bin/env bash
# 產生全量 seed（plain SQL + gzip）供分發 —— 供維護者本機執行（非 UI/後台功能）。
#
# 用法：
#   ./scripts/dev/dump-seed.sh              # → docker/seed/seed.sql.gz
#   ./scripts/dev/dump-seed.sh --sha        # 同上並印出 sha256（設進 SEED_SHA256 供 fetch-seed 校驗）
#
# 產物特性：
#   - --clean --if-exists：還原時先 DROP 再建，可重複灌入
#   - 含 alembic_version=head：還原後 alembic upgrade head 自動 no-op
#   - --no-owner --no-privileges：跨機還原免 role/權限問題
#   分發：上傳至網盤 / GitHub Release，取得連結後 export SEED_URL=... 供 fetch-seed.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

DB_NAME="kkdb_ai_quality"
OUT="docker/seed/seed.sql.gz"
SHOW_SHA=0
[ "${1:-}" = "--sha" ] && SHOW_SHA=1

command -v pg_dump >/dev/null 2>&1 || { echo "❌ 需 pg_dump（PostgreSQL client）" >&2; exit 1; }
mkdir -p "$(dirname "$OUT")"

echo "▶ pg_dump $DB_NAME → $OUT ..."
pg_dump --no-owner --no-privileges --clean --if-exists -Fp -d "$DB_NAME" | gzip -9 > "$OUT"
echo "✓ 完成：$OUT（$(du -h "$OUT" | cut -f1)）"

if [ "$SHOW_SHA" = 1 ]; then
  SHA="$(shasum -a 256 "$OUT" | awk '{print $1}')"
  echo "sha256: $SHA"
  echo "→ 供 fetch-seed.sh 校驗：export SEED_SHA256=$SHA"
fi

#!/usr/bin/env bash
# Rule 覆蓋率 + 品質稽核報表（Phase A · 純讀取）。
#   ./scripts/audit/rule_audit.sh
# 靜態品質稽核不需 DB;命中覆蓋率需後端 PG 可達(不可達則該段 skipped)。
# 用 backend venv(含 sqlalchemy + app.*),於 repo 根執行。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
if [ ! -x "$ROOT/backend/.venv/bin/python" ]; then
  echo "❌ 尚無 backend/.venv;先跑 ./start.sh 或 backend/run.sh 建環境" >&2
  exit 1
fi
exec "$ROOT/backend/.venv/bin/python" "$ROOT/scripts/audit/rule_audit.py" "$@"

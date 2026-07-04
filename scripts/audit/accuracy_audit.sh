#!/usr/bin/env bash
# 初判歸因 label-free 準確度報表（Phase D · 純讀取）。
#   ./scripts/audit/accuracy_audit.sh
# 需後端 PG 可達(不可達則報表標 skipped)＋ cleanlab(pip install -e "backend[accuracy]" 或已裝)。
# 用 backend venv(含 sqlalchemy + cleanlab + app.*),於 repo 根執行。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
if [ ! -x "$ROOT/backend/.venv/bin/python" ]; then
  echo "❌ 尚無 backend/.venv;先跑 ./scripts/dev/dev.sh 或 backend/run.sh 建環境" >&2
  exit 1
fi
exec "$ROOT/backend/.venv/bin/python" "$ROOT/scripts/audit/accuracy_audit.py" "$@"

#!/usr/bin/env bash
# rule 反哺飛輪：挑邊界誤判候選 / 精煉某 node canon 寫回 DB active 版。
#   ./scripts/refeed/rule_refeed.sh                                  # 印反哺候選
#   ./scripts/refeed/rule_refeed.sh --apply C-1 C-1-1-4 "精煉後 canon"  # 寫回並熱重載
# 需後端 PG 可達（撈 judgments）；用 backend venv（含 sqlalchemy + app.*）。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
if [ ! -x "$ROOT/backend/.venv/bin/python" ]; then
  echo "❌ 尚無 backend/.venv；先跑 ./start.sh 或 backend/run.sh 建環境" >&2
  exit 1
fi
exec "$ROOT/backend/.venv/bin/python" "$ROOT/scripts/refeed/rule_refeed.py" "$@"

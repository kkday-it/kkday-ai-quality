#!/usr/bin/env bash
# Lint 前後端（backend ruff + frontend eslint）。
#   ./scripts/dev/lint.sh
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
fail=0

echo "🐍 backend ruff ..."
if [ -x "$ROOT/backend/.venv/bin/ruff" ]; then
  ( cd "$ROOT/backend" && .venv/bin/ruff check . ) || fail=1
else
  echo "  ⚠️ 無 backend/.venv/bin/ruff（先跑 ./start.sh 或 backend/run.sh 建 venv）；略過後端"
fi

echo ""
echo "🎨 frontend eslint ..."
( cd "$ROOT/frontend" && pnpm lint ) || fail=1

echo ""
if [ "$fail" = 0 ]; then echo "✅ lint 全過"; else echo "❌ lint 有問題（見上）"; exit 1; fi

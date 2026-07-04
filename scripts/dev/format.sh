#!/usr/bin/env bash
# 統一格式化前後端（前端 Prettier + 後端 ruff format；鏈式/長行自動換行）。
#   ./scripts/dev/format.sh
# 一次性前置：frontend 跑 `pnpm install`；backend 跑 `pip install -e ".[dev]"`（含 ruff）。
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
fail=0

echo "🎨 frontend prettier ..."
if [ -x "$ROOT/frontend/node_modules/.bin/prettier" ]; then
  ( cd "$ROOT/frontend" && pnpm format ) || fail=1
else
  echo "  ⚠️ 無 prettier；先在 frontend/ 跑 pnpm install" >&2
fi

echo ""
echo "🐍 backend ruff format ..."
if [ -x "$ROOT/backend/.venv/bin/ruff" ]; then
  ( cd "$ROOT/backend" && .venv/bin/ruff format . ) || fail=1
else
  echo "  ⚠️ 無 ruff；backend 跑 .venv/bin/pip install -e \".[dev]\"（run.sh 只裝執行依賴，未含 dev）" >&2
fi

echo ""
if [ "$fail" = 0 ]; then echo "✅ 格式化完成"; else echo "❌ 部分未執行（見上）"; exit 1; fi

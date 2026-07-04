#!/usr/bin/env bash
# 重置 mock 判決資料（seed_mock.py：20 筆覆蓋 5 商品/8 維度/6 verdict/3 管道/5 狀態）。
#   ./scripts/dev/seed.sh
# 薄 wrapper：seed_mock.py 需 backend venv + import app.*，故在 backend/ 下執行。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT/backend"
if [ ! -x .venv/bin/python ]; then
  echo "❌ 尚無 backend/.venv；先跑 ./scripts/dev/dev.sh 或 backend/run.sh 建環境" >&2
  exit 1
fi
exec .venv/bin/python seed_mock.py

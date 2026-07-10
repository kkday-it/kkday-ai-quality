#!/usr/bin/env bash
# 批量 product_reviews L3 預判歸因（config/ai_judge 厚判準 + 信心度）。
#   ./scripts/tools/prejudge_reviews.sh --limit 2000
#   ./scripts/tools/prejudge_reviews.sh --limit 20            # pilot
#   ./scripts/tools/prejudge_reviews.sh --csv <path> --out <path>
# 薄 wrapper：prejudge_reviews.py 需 backend venv + import app.*，故在 backend/ 下執行。
# 真實 LLM 判決需先於設定面板填 token（否則走 stub 低信心啟發式）。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT/backend"
if [ ! -x .venv/bin/python ]; then
  echo "❌ 尚無 backend/.venv；先跑 ./start.sh 或 backend/run.sh 建環境" >&2
  exit 1
fi
exec .venv/bin/python prejudge_reviews.py "$@"

#!/usr/bin/env bash
# 後端 smoke test（零 key stub，端到端走通判決鏈）。
#   ./scripts/dev/test.sh
# 薄 wrapper：smoke_test.py 需 backend venv + import app.*，故委派 backend/run.sh test。
# ⚠️ 已知債：環境若已設 OPENAI_API_KEY 會跑真 LLM、且 /api/diagnose 需 auth → 部分斷言會失敗
#    （見 docs/CODE-REVIEW-2026-06-26.md D 區「smoke_test 缺 auth」待修）。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT/backend"
exec ./run.sh test

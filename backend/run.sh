#!/usr/bin/env bash
# 一鍵啟動 / 測試 AI 法官後端
#   ./run.sh        建 venv + 裝依賴 + 啟動 API（port 8100）
#   ./run.sh test   建 venv + 裝依賴 + 跑 smoke test（零 key，stub）
set -euo pipefail
cd "$(dirname "$0")"

# 1. venv（首次自動建）
if [ ! -d .venv ]; then
  echo "🔧 建立 venv (.venv)..."
  python3 -m venv .venv
fi

# 2. 依賴（冪等，已裝則快）
echo "📦 安裝依賴..."
.venv/bin/pip install -q -e .

# 3. test 模式 or 啟動
if [ "${1:-}" = "test" ]; then
  echo "🧪 smoke test..."
  exec .venv/bin/python smoke_test.py
fi

echo "🚀 AI 法官後端啟動：http://localhost:8100  (Swagger: http://localhost:8100/docs)"
echo "   無 OPENAI_API_KEY → stub 模式；設 key 後自動切真 LLM。"
exec .venv/bin/uvicorn app.api.main:app --reload --port 8100

#!/usr/bin/env bash
# 後端容器啟動前置：跑 DB migration，再交棒給 CMD（uvicorn）。
# 生產 schema 演進唯一入口——確保程式碼與資料庫結構同版本後才對外服務。
set -euo pipefail

echo "▶ alembic upgrade head ..."
python -m alembic upgrade head

echo "▶ 啟動：$*"
exec "$@"

#!/usr/bin/env bash
# 後端容器啟動前置：對齊 DB schema，再交棒給 CMD（uvicorn）。
# 生產 schema 演進唯一入口——確保程式碼與資料庫結構同版本後才對外服務。
#
# 判斷邏輯（squash 相容 / 未蓋章的既有庫 / 認不得的版本）在 app.core.db.schema_bootstrap，
# 不寫在這裡：那是有分支有失敗模式的邏輯，寫成 bash heredoc 無法單元測試
# （見 tests/test_schema_bootstrap.py）。
set -euo pipefail

python -m app.core.db.schema_bootstrap

echo "▶ 啟動：$*"
exec "$@"

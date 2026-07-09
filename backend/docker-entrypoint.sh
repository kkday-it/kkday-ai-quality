#!/usr/bin/env bash
# 後端容器啟動前置：對齊 DB schema，再交棒給 CMD（uvicorn）。
# 生產 schema 演進唯一入口——確保程式碼與資料庫結構同版本後才對外服務。
#
# ⚠️ 為何不無條件 `alembic upgrade head`：migration 鏈含「假設 create_all 既有表」的破壞性 DDL
#   （c24e5b0964ce 對 prod_quality DROP INDEX、c7ae2e2be254 對 prod_quality/pkg_quality DROP COLUMN），
#   這些表從無 create migration（僅 metadata.create_all 產生）→ 對真正空庫跑鏈會 crash。
#   設計上 fresh 庫走 create_all + stamp head（＝ app.core.db.init_db，與 dev/CI 同一 SSOT），
#   既有庫（已有 alembic_version 記錄）才跑增量 upgrade head。此分流與 ingest.py init_db 語意一致。
set -euo pipefail

# 判斷 DB 是否已被管控（alembic_version 有列）：有→既有庫走增量 upgrade；無→空庫走 create_all+stamp。
if python -c "
import sys
from sqlalchemy import inspect, text
from app.core.db import tables as T

engine = T.get_engine()
insp = inspect(engine)
populated = False
if insp.has_table('alembic_version'):
    with engine.connect() as conn:
        populated = (conn.execute(text('SELECT count(*) FROM alembic_version')).scalar() or 0) > 0
sys.exit(0 if populated else 1)
"; then
  echo "▶ 既有庫：alembic upgrade head（增量套用 migration）..."
  python -m alembic upgrade head
else
  echo "▶ 空庫：create_all + stamp head（migration 鏈不可從零 upgrade，走 init_db 與 dev/CI 同源）..."
  python -c "from app.core.db import init_db; init_db()"
fi

echo "▶ 啟動：$*"
exec "$@"

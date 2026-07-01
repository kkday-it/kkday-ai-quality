#!/usr/bin/env bash
# 環境自檢閘門 —— dev.sh / 部署前置。偵測工具鏈 + 依賴 + DB + migration，冪等可重跑。
#   ./scripts/doctor.sh          檢測 + 冪等自動修復（cp .env / 裝依賴 / alembic upgrade）
#   ./scripts/doctor.sh --check  唯讀檢測（CI / prod：不改檔、不裝、不 upgrade），有問題回非零
#
# 設計原則（呼應專案反 over-engineering）：
#   - 系統級工具（python / node / pnpm / PostgreSQL）缺失 → 只「報告 + 給安裝指令」，不擅自裝（跨平台太脆）
#   - 項目內、冪等、無副作用者（venv / pip / pnpm / .env / alembic）→ 預設自動修復，--check 模式僅報告
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

CHECK_ONLY=0
[ "${1:-}" = "--check" ] && CHECK_ONLY=1

FAIL=0
sec()  { printf '\n\033[1m%s\033[0m\n' "$1"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
warn() { printf '  \033[33m⚠\033[0m %s\n' "$1"; }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$1"; FAIL=1; }
# 修復動作：--check 模式下改為「僅提示、不執行」
fix()  { if [ "$CHECK_ONLY" = 1 ]; then warn "需修復（--check 不自動執行）：$1"; return 1; fi; return 0; }

# ── 1. 工具鏈（缺失只報告 + 指令，不自動裝）──────────────────────────────────
sec "① 工具鏈"
if command -v python3 >/dev/null && python3 -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3, 10) else 1)' 2>/dev/null; then
  ok "python3 $(python3 -V 2>&1 | awk '{print $2}')（需 ≥ 3.10）"
else
  bad "python3 ≥ 3.10 缺失 → macOS: brew install python@3.12 ｜ Debian: apt install python3.12"
fi

NODE_MAJOR="$(node -v 2>/dev/null | sed 's/v\([0-9]*\).*/\1/')"
if [ -n "$NODE_MAJOR" ] && [ "$NODE_MAJOR" -ge 20 ] 2>/dev/null; then
  ok "node $(node -v)（需 ≥ 20）"
else
  bad "node ≥ 20 缺失 → brew install node ｜ 或 nvm install 20"
fi

if command -v pnpm >/dev/null; then
  ok "pnpm $(pnpm -v)"
else
  bad "pnpm 缺失 → npm i -g pnpm ｜ 或 corepack enable"
fi

# ── 2. backend/.env（機密設定；不存在則從範本生成並提示填值）────────────────
sec "② backend/.env"
if [ -f backend/.env ]; then
  ok "backend/.env 已存在"
  grep -qE '^AIQ_JWT_SECRET=.+' backend/.env || warn "AIQ_JWT_SECRET 未填（dev 用 fallback；staging/production 缺此值會拒啟動）"
elif fix "cp backend/.env.example → backend/.env"; then
  cp backend/.env.example backend/.env && warn "已生成 backend/.env，請填 AIQ_JWT_SECRET / OPENAI_API_KEY 等機密"
fi

# ── 3. 依賴（venv + backend[dev]、frontend frozen-lockfile；冪等）─────────────
sec "③ 依賴"
if [ -d backend/.venv ]; then
  ok "backend venv 已建（pyproject 為唯一真相源）"
  [ "$CHECK_ONLY" = 0 ] && (cd backend && .venv/bin/pip install -q -e ".[dev]") && ok "backend 依賴已同步"
elif fix "建立 backend venv + pip install -e .[dev]"; then
  (cd backend && python3 -m venv .venv && .venv/bin/pip install -q -e ".[dev]") && ok "已建 venv + 裝依賴"
fi
if [ -d frontend/node_modules ]; then
  ok "frontend node_modules 已存在"
  [ "$CHECK_ONLY" = 0 ] && (cd frontend && pnpm install --frozen-lockfile --silent) && ok "frontend 依賴已同步"
elif fix "frontend pnpm install --frozen-lockfile"; then
  (cd frontend && pnpm install --frozen-lockfile --silent) && ok "frontend 依賴就緒"
fi

# ── 4. PostgreSQL 連線（讀 config.env.database_url 實測）──────────────────────
sec "④ 資料庫連線"
if [ -x backend/.venv/bin/python ]; then
  DB_URL="$(cd backend && .venv/bin/python -c 'from app.core.config import env; print(env.database_url)' 2>/dev/null)"
  if (cd backend && .venv/bin/python -c 'from sqlalchemy import create_engine, text; from app.core.config import env; create_engine(env.database_url).connect().execute(text("select 1"))' 2>/dev/null); then
    ok "PostgreSQL 可連（${DB_URL%%\?*}）"
  else
    bad "PostgreSQL 連不上（${DB_URL%%\?*}）→ 本機: brew services start postgresql@17 ｜ 或設 DATABASE_URL env"
  fi
else
  warn "venv 未就緒，略過 DB 檢查（先過 ③）"
fi

# ── 5. Alembic migration（current vs heads；落後則升級或提示）─────────────────
sec "⑤ Schema migration"
if [ -x backend/.venv/bin/python ] && [ "${DB_URL:-}" ]; then
  CUR="$(cd backend && .venv/bin/python -m alembic current 2>/dev/null | grep -oE '[0-9a-f]{12}' | tail -1)"
  HEAD="$(cd backend && .venv/bin/python -m alembic heads 2>/dev/null | grep -oE '[0-9a-f]{12}' | tail -1)"
  if [ -n "${HEAD}" ] && [ "${CUR}" = "${HEAD}" ]; then
    ok "已在最新 migration（${HEAD}）"
  elif [ -z "${HEAD}" ]; then
    warn "無法讀取 alembic heads（DB 或設定異常）"
  elif fix "alembic upgrade head（${CUR} → ${HEAD}）"; then
    (cd backend && .venv/bin/python -m alembic upgrade head) && ok "已升級至 ${HEAD}"
  else
    bad "migration 落後（current=${CUR} head=${HEAD}）→ 部署流程須跑 alembic upgrade head"
  fi
else
  warn "略過 migration 檢查（依賴 ③④ 先通過）"
fi

# ── 總結 ─────────────────────────────────────────────────────────────────────
sec "結果"
if [ "$FAIL" = 0 ]; then
  ok "環境自檢通過"; exit 0
else
  bad "存在阻擋項，請依上方指令修復後重跑"; exit 1
fi

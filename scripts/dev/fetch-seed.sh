#!/usr/bin/env bash
# 取得（並可選還原）資料庫 seed —— 多源、冪等。
#
# 用法：
#   ./scripts/dev/fetch-seed.sh                 # 只確保 docker/seed/seed.sql.gz 存在（供 docker compose 用）
#   ./scripts/dev/fetch-seed.sh --restore-if-empty  # 取得 + 本機 DB 為空時還原（start.sh onboarding 用）
#   ./scripts/dev/fetch-seed.sh --sample        # 改取精簡樣本（SEED_SAMPLE_URL）
#
# 多維度來源（依序嘗試，任一成功即止）：
#   1. 本地已存在且（若設 SEED_SHA256）checksum 符 → 直接用
#   2. Git LFS 追蹤且已 pull（檔案非 pointer）→ 直接用
#   3. SEED_URL（任意 HTTP/HTTPS：網盤直連 / GitHub Release / GCS·S3 presigned）→ 下載
#   皆無 → 回報缺失（非致命；呼叫端可決定略過）
#
# 環境變數：
#   SEED_URL         全量 seed 下載連結（curl/wget 可取）
#   SEED_SAMPLE_URL  --sample 模式的樣本連結
#   SEED_SHA256      （選）下載後校驗
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

SEED_FILE="docker/seed/seed.sql.gz"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.dev.yml}"
DB_SERVICE="db"
DB_NAME="kkdb_ai_quality"
MODE_RESTORE=0
USE_SAMPLE=0
for arg in "$@"; do
  case "$arg" in
    --restore-if-empty) MODE_RESTORE=1 ;;
    --sample)           USE_SAMPLE=1 ;;
    *) echo "未知參數：$arg" >&2; exit 2 ;;
  esac
done

mkdir -p "$(dirname "$SEED_FILE")"

_looks_like_lfs_pointer() {
  # 未 pull 的 LFS 檔開頭為 "version https://git-lfs..."；真 gzip 檔開頭為 magic bytes
  head -c 40 "$1" 2>/dev/null | grep -q "git-lfs" && return 0 || return 1
}

_verify_checksum() {
  [ -z "${SEED_SHA256:-}" ] && return 0
  local got
  got="$(shasum -a 256 "$SEED_FILE" 2>/dev/null | awk '{print $1}')"
  [ "$got" = "$SEED_SHA256" ]
}

_download() {
  local url="$1"
  echo "⬇️  下載 seed：${url%%\?*} ..."
  if command -v curl >/dev/null 2>&1; then
    curl -fL --retry 3 -o "$SEED_FILE" "$url"
  elif command -v wget >/dev/null 2>&1; then
    wget -q -O "$SEED_FILE" "$url"
  else
    echo "❌ 需 curl 或 wget 才能下載 seed" >&2; return 1
  fi
}

_ensure_seed_present() {
  local url="$SEED_URL"
  [ "$USE_SAMPLE" = 1 ] && url="${SEED_SAMPLE_URL:-}"

  # 1) 本地已存在（且非 LFS pointer、checksum 符）
  if [ -s "$SEED_FILE" ] && ! _looks_like_lfs_pointer "$SEED_FILE"; then
    if _verify_checksum; then
      echo "✓ 已有本地 seed：$SEED_FILE"; return 0
    else
      echo "⚠️ 本地 seed checksum 不符，重新下載"
    fi
  fi
  # 2) LFS pointer → 提示 pull
  if [ -f "$SEED_FILE" ] && _looks_like_lfs_pointer "$SEED_FILE"; then
    echo "ℹ️ seed 為 Git LFS pointer，嘗試 git lfs pull ..."
    command -v git-lfs >/dev/null 2>&1 && git lfs pull --include="$SEED_FILE" 2>/dev/null
    [ -s "$SEED_FILE" ] && ! _looks_like_lfs_pointer "$SEED_FILE" && { echo "✓ LFS 已拉取"; return 0; }
  fi
  # 3) 下載
  if [ -n "$url" ]; then
    _download "$url" || return 1
    _verify_checksum || { echo "❌ 下載後 checksum 不符" >&2; return 1; }
    echo "✓ seed 已下載：$SEED_FILE"; return 0
  fi
  echo "⚠️ 無可用 seed 來源（未設 SEED_URL、無本地檔、無 LFS）。" >&2
  echo "   → 提供你的 seed 連結：export SEED_URL='<網盤/Release 連結>' 後重試；或本機自產：./scripts/dev/dump-seed.sh" >&2
  return 1
}

_db_is_empty() {
  # 以 product_reviews 是否存在為「空庫」判準（seed 還原後即存在）
  local reg
  reg="$(docker compose -f "$COMPOSE_FILE" exec -T "$DB_SERVICE" \
    psql -U postgres -tAqc "SELECT to_regclass('public.product_reviews')" -d "$DB_NAME" 2>/dev/null | tr -d '[:space:]')"
  [ -z "$reg" ] || [ "$reg" = "" ]
}

_restore() {
  echo "♻️  還原 seed → $DB_NAME ..."
  gunzip -c "$SEED_FILE" | docker compose -f "$COMPOSE_FILE" exec -T "$DB_SERVICE" \
    psql -U postgres -v ON_ERROR_STOP=1 -d "$DB_NAME" >/dev/null
  echo "✓ seed 還原完成"
}

# ── 主流程 ──
if ! _ensure_seed_present; then
  # 取得失敗：restore 模式視為非致命略過（空庫 → 由 backend create_all 建空 schema）
  [ "$MODE_RESTORE" = 1 ] && { echo "↷ 無 seed 可還原，略過（將以空庫啟動）"; exit 0; }
  exit 1
fi

if [ "$MODE_RESTORE" = 1 ]; then
  if _db_is_empty; then
    _restore
  else
    echo "✓ DB 非空，跳過 seed 還原（避免覆蓋既有數據）"
  fi
fi

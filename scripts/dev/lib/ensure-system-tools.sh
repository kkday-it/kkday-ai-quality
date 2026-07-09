#!/usr/bin/env bash
# 系統級工具偵測 + 協助安裝（供 start.sh 於 doctor.sh 之前 source）。
#
# 設計分工（刻意）：
#   - doctor.sh 維持「檢查閘門（report-only）」，不動使用者系統。
#   - 本 lib 負責「缺才裝」的實際安裝動作，集中一處、可審。
#
# 跨平台策略（安全優先，不亂猜）：
#   - macOS → brew ｜ Debian/Ubuntu → apt ｜ Fedora/RHEL → dnf ｜ Arch → pacman
#   - 未知平台 / macOS 無 brew → 只印官方指令並回報失敗，不擅自安裝（跨平台自動裝太脆）
#   - 每個缺失工具「裝前二次確認」（動使用者系統）；非互動環境需 AIQ_AUTO_INSTALL=1 才裝
#   - 冪等：已就緒則跳過
#
# 對外唯一入口：ensure_system_tools  → 回 0 全就緒 / 非 0 有阻擋項

# 注意：本檔被 source，不自行 set -e，避免污染呼叫端；錯誤以回傳碼傳遞。

_est_ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
_est_warn() { printf '  \033[33m!\033[0m %s\n' "$1"; }
_est_bad()  { printf '  \033[31m✗\033[0m %s\n' "$1" >&2; }

# 偵測平台套件管理員：brew / apt / dnf / pacman / none-mac / none-linux / none
_est_pkg_mgr() {
  case "$(uname -s)" in
    Darwin)
      if command -v brew >/dev/null 2>&1; then echo brew; else echo none-mac; fi ;;
    Linux)
      local id="" like=""
      if [ -r /etc/os-release ]; then
        # shellcheck disable=SC1091
        . /etc/os-release; id="${ID:-}"; like="${ID_LIKE:-}"
      fi
      case "${id}${like}" in
        *debian*|*ubuntu*)        echo apt ;;
        *fedora*|*rhel*|*centos*) echo dnf ;;
        *arch*)                   echo pacman ;;
        *)                        echo none-linux ;;
      esac ;;
    *) echo none ;;
  esac
}

# 裝前確認（動使用者系統）；非互動須 AIQ_AUTO_INSTALL=1
_est_confirm_install() {
  local what="$1"
  if [ "${AIQ_AUTO_INSTALL:-0}" = 1 ]; then return 0; fi
  if [ ! -t 0 ]; then
    _est_warn "非互動環境；設 AIQ_AUTO_INSTALL=1 才自動安裝「${what}」，本次改用手動指令。"
    return 1
  fi
  printf '  偵測到缺少 %s，是否自動安裝？[Y/n] ' "$what"
  local ans; read -r ans
  case "${ans:-Y}" in [Nn]*) return 1 ;; *) return 0 ;; esac
}

# 以平台套件管理員安裝套件
_est_install() {
  local mgr="$1"; shift
  case "$mgr" in
    brew)   brew install "$@" ;;
    apt)    sudo apt-get update -qq && sudo apt-get install -y "$@" ;;
    dnf)    sudo dnf install -y "$@" ;;
    pacman) sudo pacman -S --noconfirm "$@" ;;
    *)      return 1 ;;
  esac
}

_est_ensure_python() {
  if command -v python3 >/dev/null 2>&1 \
     && python3 -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3, 10) else 1)' 2>/dev/null; then
    _est_ok "python3 $(python3 -V 2>&1 | awk '{print $2}')（需 ≥ 3.10）"; return 0
  fi
  _est_warn "python3 ≥ 3.10 缺失"
  local mgr; mgr="$(_est_pkg_mgr)"
  if _est_confirm_install "python 3.12"; then
    case "$mgr" in
      brew)   _est_install brew python@3.12 ;;
      apt)    _est_install apt python3 python3-venv python3-pip ;;
      dnf)    _est_install dnf python3 ;;
      pacman) _est_install pacman python ;;
      *)      _est_bad "未知平台，請手動安裝 python ≥ 3.10"; return 1 ;;
    esac
  else
    _est_bad "python 未裝 → macOS: brew install python@3.12 ｜ Debian: apt install python3"; return 1
  fi
  command -v python3 >/dev/null 2>&1 && _est_ok "python3 就緒"
}

_est_ensure_node() {
  local major; major="$(node -v 2>/dev/null | sed 's/v\([0-9]*\).*/\1/')"
  if [ -n "$major" ] && [ "$major" -ge 20 ] 2>/dev/null; then
    _est_ok "node $(node -v)（需 ≥ 20）"; return 0
  fi
  _est_warn "node ≥ 20 缺失"
  local mgr; mgr="$(_est_pkg_mgr)"
  if _est_confirm_install "node 22"; then
    case "$mgr" in
      brew)   _est_install brew node ;;
      apt)    _est_install apt nodejs npm
              _est_warn "apt 的 nodejs 版本可能 < 20；若過舊請改用 nvm（nvm install 22）或 NodeSource repo" ;;
      dnf)    _est_install dnf nodejs ;;
      pacman) _est_install pacman nodejs npm ;;
      *)      _est_bad "未知平台，請手動安裝 node ≥ 20（建議 nvm install 22）"; return 1 ;;
    esac
  else
    _est_bad "node 未裝 → brew install node ｜ 或 nvm install 22"; return 1
  fi
  command -v node >/dev/null 2>&1 && _est_ok "node 就緒"
}

_est_ensure_pnpm() {
  if command -v pnpm >/dev/null 2>&1; then _est_ok "pnpm $(pnpm -v)"; return 0; fi
  _est_warn "pnpm 缺失"
  # 優先走 corepack（隨 node 附帶，最乾淨，鎖 package.json packageManager 版本）
  if command -v corepack >/dev/null 2>&1; then
    if corepack enable >/dev/null 2>&1 && command -v pnpm >/dev/null 2>&1; then
      _est_ok "pnpm 經 corepack 啟用"; return 0
    fi
  fi
  if _est_confirm_install "pnpm"; then
    npm i -g pnpm && command -v pnpm >/dev/null 2>&1 && { _est_ok "pnpm 已裝"; return 0; }
  fi
  _est_bad "pnpm 未裝 → corepack enable ｜ 或 npm i -g pnpm"; return 1
}

_est_ensure_postgres() {
  # 伺服器就緒？（沿用 doctor.sh 精神：能連即可）
  if command -v pg_isready >/dev/null 2>&1 && pg_isready -q 2>/dev/null; then
    _est_ok "PostgreSQL 伺服器就緒"
  else
    _est_warn "PostgreSQL 未就緒"
    local mgr; mgr="$(_est_pkg_mgr)"
    if _est_confirm_install "PostgreSQL 17"; then
      case "$mgr" in
        brew)   _est_install brew postgresql@17 && brew services start postgresql@17 ;;
        apt)    _est_install apt postgresql && sudo service postgresql start ;;
        dnf)    _est_install dnf postgresql-server \
                  && sudo postgresql-setup --initdb 2>/dev/null; sudo systemctl start postgresql ;;
        pacman) _est_install pacman postgresql && sudo systemctl start postgresql ;;
        *)      _est_bad "未知平台，請手動安裝並啟動 PostgreSQL 17"; return 1 ;;
      esac
      sleep 2
    else
      _est_bad "PostgreSQL 未就緒 → macOS: brew services start postgresql@17 ｜ Debian: sudo service postgresql start"
      return 1
    fi
  fi
  # 建庫（若不存在）——macOS brew 下連線角色＝當前 user；Linux 可能需先建 role（失敗給提示）
  if psql -lqt 2>/dev/null | cut -d'|' -f1 | grep -qw kkdb_ai_quality; then
    _est_ok "DB kkdb_ai_quality 已存在"
  elif createdb kkdb_ai_quality 2>/dev/null; then
    _est_ok "已建立 DB kkdb_ai_quality"
  else
    _est_warn "createdb kkdb_ai_quality 未成功（Linux 常因 peer auth 需先建同名 role：sudo -u postgres createuser -s \"\$USER\"）"
  fi
}

# 對外入口
ensure_system_tools() {
  echo "🔧 系統工具檢查（缺才裝，裝前二次確認；非互動設 AIQ_AUTO_INSTALL=1）..."
  local rc=0
  _est_ensure_python   || rc=1
  _est_ensure_node     || rc=1
  _est_ensure_pnpm     || rc=1
  _est_ensure_postgres || rc=1
  return "$rc"
}

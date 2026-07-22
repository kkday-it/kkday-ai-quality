"""本地直接授予 provider：無角色，email 對照 config/global/permissions.json。

現行資料形狀 = email → business-key 集合（`default ∪ grants[email]`），與 be2 business-list
天然同構——be2 直接下發每 user 一組 permission 字串（無角色中間層），adapter 接入時只換
資料來源、判斷邏輯不動。`no_auth_grant_all=true` 時無條件全通過（本地尚未接 SSO，無法辨識
使用者身分）；`grants[email]` 含 `'*'` 展開為 ALL_KEYS。permissions.json 缺 / 壞 → 回退空集合
（**fail-closed**：無法判定即無權限，不放行）。
"""

from __future__ import annotations

import json
import logging

from app.core.paths import GLOBAL_DIR

from .permission_keys import ALL_KEYS

_log = logging.getLogger(__name__)

# 模組級快取；編輯 permissions.json 後呼叫 reload()。
_CACHE: dict | None = None


def _permissions_cfg() -> dict:
    """讀 config/global/permissions.json（lazy 快取；檔缺 / 壞回空 dict → 全員無 key·fail-closed）。"""
    global _CACHE
    if _CACHE is None:
        try:
            _CACHE = json.loads((GLOBAL_DIR / "permissions.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _log.warning("permissions.json 缺失或格式錯誤，全員視為無任何權限（fail-closed）")
            _CACHE = {}
    return _CACHE


def reload() -> None:
    """清 permissions 快取（編輯 permissions.json 後呼叫；或重啟 server）。"""
    global _CACHE
    _CACHE = None


class LocalPermissionProvider:
    """config 直接授予 provider（實作 PermissionProvider Protocol）。"""

    def get_permissions(self, user: dict) -> set[str]:
        """user email → default ∪ grants[email]；no_auth_grant_all 或 '*' 命中 → 全量。"""
        cfg = _permissions_cfg()
        if cfg.get("no_auth_grant_all"):
            return set(ALL_KEYS)
        email = str(user.get("email") or "").strip().lower()
        grants = {str(k).strip().lower(): v for k, v in (cfg.get("grants") or {}).items()}
        keys = set(cfg.get("default") or []) | set(grants.get(email) or [])
        if "*" in keys:  # grants 慣例：全量權限
            return set(ALL_KEYS)
        # 過濾未知 key：config 打錯的字串不放行為權限（避免拼錯 key 意外授權）。
        return {k for k in keys if k in ALL_KEYS}

    def check(self, user: dict, permission: str) -> bool:
        """該 user 是否具備某 business-key 權限。"""
        return permission in self.get_permissions(user)

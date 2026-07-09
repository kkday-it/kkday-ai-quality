"""本地 config 白名單 provider：角色（roles.json）→ 權限 key（role_permissions.json）。

現行單機兩級（admin/qc）的權限效果，但**資料形狀 = 角色→business-key 集合**，與 be2 一致。
admin 慣例值 `'*'` 展開為 ALL_KEYS（前後端 includes 語意一致）。role_permissions.json 缺 / 壞 →
回退空集合（**fail-closed**：無法判定即無權限，不放行）。
"""

from __future__ import annotations

import json
import logging

from app.core import auth
from app.core.paths import GLOBAL_DIR

from .permission_keys import ALL_KEYS

_log = logging.getLogger(__name__)

# 模組級快取（比照 auth._ROLES_CACHE）；編輯 role_permissions.json 後呼叫 reload()。
_CACHE: dict | None = None


def _role_permissions_cfg() -> dict:
    """讀 config/global/role_permissions.json（lazy 快取；檔缺 / 壞回空 dict → 全員無 key·fail-closed）。"""
    global _CACHE
    if _CACHE is None:
        try:
            _CACHE = json.loads((GLOBAL_DIR / "role_permissions.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _log.warning("role_permissions.json 缺失或格式錯誤，全員視為無任何權限（fail-closed）")
            _CACHE = {}
    return _CACHE


def reload() -> None:
    """清 role_permissions 快取（編輯 role_permissions.json 後呼叫；或重啟 server）。"""
    global _CACHE
    _CACHE = None


class LocalPermissionProvider:
    """config 白名單 provider（實作 PermissionProvider Protocol）。"""

    def get_permissions(self, user: dict) -> set[str]:
        """user email → 角色（auth.role_for）→ role_permissions.json 的 key 集合；admin '*' 展全量。"""
        role = auth.role_for(user.get("email"))
        keys = _role_permissions_cfg().get(role, [])
        if "*" in keys:  # admin 慣例：全量權限
            return set(ALL_KEYS)
        # 過濾未知 key：config 打錯的字串不放行為權限（避免拼錯 key 意外授權）。
        return {k for k in keys if k in ALL_KEYS}

    def check(self, user: dict, permission: str) -> bool:
        """該 user 是否具備某 business-key 權限。"""
        return permission in self.get_permissions(user)

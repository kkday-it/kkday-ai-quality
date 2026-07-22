"""權限 dependency：`require_permission`（FastAPI 依賴工廠）+ `get_provider`（唯一替換點）。

router 只掛 `require_permission(key)`，不綁具體 provider；換 be2 只改 auth.config.json 與
be2_provider.py（get_provider 是唯一分流處），router 全不動。
"""

from __future__ import annotations

import json
import logging

from fastapi import Depends, HTTPException

from app.core import auth
from app.core.paths import GLOBAL_DIR

from .base import PermissionProvider
from .be2_provider import Be2PermissionProvider
from .local_provider import LocalPermissionProvider

_log = logging.getLogger(__name__)

_AUTH_CFG_CACHE: dict | None = None


def auth_config() -> dict:
    """讀 config/global/auth.config.json（authProvider/provider 切換開關 + be2 佔位段）；缺 / 壞回 local 預設。

    公開供 core.auth_verifiers（登入 verifier 分流）與其他消費端共用（單一讀取器 + reload 快取）。
    """
    global _AUTH_CFG_CACHE
    if _AUTH_CFG_CACHE is None:
        try:
            _AUTH_CFG_CACHE = json.loads(
                (GLOBAL_DIR / "auth.config.json").read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            _AUTH_CFG_CACHE = {"provider": "local"}
    return _AUTH_CFG_CACHE


def reload() -> None:
    """清 auth.config 快取（編輯 auth.config.json 後呼叫；或重啟 server）。"""
    global _AUTH_CFG_CACHE
    _AUTH_CFG_CACHE = None


def business_list_ttl_ms() -> int:
    """前端權限清單（be2 auth.business-list）快取 TTL 毫秒（auth.config.json；預設 12 小時）。"""
    return int(auth_config().get("businessListTtlMs") or 43_200_000)


def get_provider() -> PermissionProvider:
    """依 auth.config.json['provider'] 選 provider——換 be2 的**唯一後端分流點**。"""
    name = str(auth_config().get("provider") or "local").lower()
    if name == "be2":
        return Be2PermissionProvider()
    return LocalPermissionProvider()


def require_permission(permission: str):
    """FastAPI 依賴工廠：先過 get_current_user 認證，再檢 business-key 權限；無權限 / 判定失敗一律 403（fail-closed）。

    用法：`user: dict = Depends(require_permission(permission_keys.FINDING_REVIEW_UPDATE))`。
    回傳 user dict 供 handler 沿用（同 get_current_user）。
    """

    def _dep(user: dict = Depends(auth.get_current_user)) -> dict:
        try:
            allowed = get_provider().check(user, permission)
        except Exception:  # noqa: BLE001 — provider 出錯一律 fail-closed，絕不因異常放行
            _log.exception("權限判定失敗（fail-closed 403）permission=%s", permission)
            raise HTTPException(status_code=403, detail="權限判定失敗") from None
        if not allowed:
            raise HTTPException(status_code=403, detail=f"缺少權限：{permission}")
        return user

    return _dep

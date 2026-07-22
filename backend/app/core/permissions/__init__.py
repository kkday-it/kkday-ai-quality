"""可替換權限框架：PermissionProvider 抽象 + require_permission dependency + business-key 常數。

換 be2 中央 Auth SVC 的唯一後端改動點 = `config/global/auth.config.json['provider']` +
`be2_provider.py`；所有 router 掛 `require_permission(key)`（不綁 provider），故切換零改 router。

用法（router）::

    from app.core.permissions import require_permission, permission_keys

    @router.post("/...")
    def handler(user: dict = Depends(require_permission(permission_keys.FINDING_REVIEW_UPDATE))):
        ...
"""

from __future__ import annotations

from . import permission_keys
from .base import PermissionProvider
from .deps import business_list_ttl_ms, get_provider, require_permission
from .deps import reload as reload_auth_config
from .local_provider import reload as reload_permissions
from .permission_keys import ALL_KEYS

__all__ = [
    "ALL_KEYS",
    "PermissionProvider",
    "business_list_ttl_ms",
    "get_provider",
    "permission_keys",
    "reload_auth_config",
    "reload_permissions",
    "require_permission",
]

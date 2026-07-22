"""be2 中央 Auth SVC provider（過渡實作）——僅 auth.config.json provider='be2' 時選用。

**過渡策略**：正式 business-list 契約接通前，get_permissions 委派 local role map
（roles.json email 白名單 → role_permissions.json）——與 LocalProvider 行為安全等價、
fail-closed 不變。這讓「登入先切 be2（authProvider）、授權沿用 local map」的漸進路徑可行，
待 auth team 契約到位後只改本檔內部實作，router 與前端 store/directive/guard 全不動
（此檔 + auth.config.json 為**唯一改動點**）。

正式路徑二選一（對齊 be2-b2c-bs/dcs/b2cbe 實證模式，接入時擇一實作）：

1. **登入 response businessList 透傳**：be2 登入/refresh 回應本身帶 `businessList: string[]`
   （`{category}.{business}.{action}`）——後端驗證 accessToken 後向 Auth SVC 取該 user 的
   business-list（server-to-server 契約待 auth team），與本專案 permission_keys 交集即權限集。
2. **每請求 verify**：打 Auth SVC `verify-be2-ci {userUuid, method, uri}` fail-closed 403 +
   whiteList（auth.config.json 已預留）——be2 base controller 模式，無需先抓全集。

token 過期語義：403 + response header `x-kkday-auth-svc-status: AU9404`（前端據此觸發
refresh，見前端 http.api.ts 續期攔截）。
"""

from __future__ import annotations

import logging

_log = logging.getLogger(__name__)


class Be2PermissionProvider:
    """be2 provider（過渡：委派 local role map；正式契約接通後改打 Auth SVC——見檔頭）。"""

    def get_permissions(self, user: dict) -> set[str]:
        """取 user 權限集。過渡期委派 LocalProvider（email 白名單 role map），行為安全等價。

        TODO(auth-team 契約)：改為向中央 Auth SVC 取該 user 的 business-list
        （server-to-server 驗證規格待索取），與 permission_keys.ALL_KEYS 交集後回傳。
        """
        from .local_provider import LocalPermissionProvider  # 延遲 import 防循環

        return LocalPermissionProvider().get_permissions(user)

    def check(self, user: dict, permission: str) -> bool:
        """權限判定（fail-closed）。過渡期以 get_permissions 集合比對；正式可改每請求 verify（檔頭路徑 2）。"""
        try:
            return permission in self.get_permissions(user)
        except Exception:  # noqa: BLE001 — 任何異常不放行
            _log.exception("be2 provider 權限判定異常（fail-closed）permission=%s", permission)
            return False

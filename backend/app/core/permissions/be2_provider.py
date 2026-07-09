"""be2 中央 Auth SVC provider（空殼·整合點文檔）——僅 auth.config.json provider='be2' 時選用。

日後串接 be2 時在此實作，router 與前端 store/directive/guard 全不動（此檔 + auth.config.json 為
**唯一改動點**）。整合面 3 點（對齊 be2-b2cbe/ci/dcs/deals 既有模式）：

1. **token get/refresh**：走 be2 API Gateway；403 + response header `x-kkday-auth-svc-status: AU9404`
   時觸發 refresh（前端負責，見前端 permission.api.ts 整合點）。
2. **權限清單來源**：呼叫中央 Auth SVC 取該 user 的 permission-string 陣列
   （`module.sub-function.action`），對映本專案 permission_keys（命名已對齊 be2 風格）。
3. **check 策略**：可覆寫為每請求 `verify-be2-ci {userUuid, method, uri}` fail-closed 403 + 白名單
   （be2 base controller 模式），而非先抓全集再本地比對——視 be2 契約決定。
"""

from __future__ import annotations


class Be2PermissionProvider:
    """be2 provider 空殼。串接前呼叫即拋 NotImplementedError（fail-closed·不誤放行）。"""

    def get_permissions(self, user: dict) -> set[str]:
        """待實作：呼叫中央 Auth SVC 取 user 的 permission-string 陣列。"""
        raise NotImplementedError(
            "be2 Auth SVC provider 尚未實作；auth.config.json 的 provider 設為 'be2' 前需先實作此類。"
        )

    def check(self, user: dict, permission: str) -> bool:
        """待實作：可覆寫為每請求打 verify-be2-ci（見檔頭整合面第 3 點）。"""
        raise NotImplementedError("be2 Auth SVC provider 尚未實作。")

"""帳號身分解析：本地模式固定身分（無登入系統，不驗 token）；be2 模式走 get_verifier() 驗簽接縫。

去帳戶系統（2026-07-22）：單機內網環境，本地不再有 register/login/登出/切換帳號/bcrypt/自建
JWT——身分僅供權限授予查詢（見 app.core.permissions，email 對照 config/global/permissions.json）
與稽核欄位（triggered_by 等），不是存取控制手段；存取控制交給 no_auth_grant_all + 權限框架。
be2 SSO 接入後（authProvider=be2）才需要真正驗證 token（見 auth_verifiers.get_verifier）。
"""

from __future__ import annotations

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import env

_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict:
    """FastAPI 認證依賴：本地模式回固定身分（忽略 token）；be2 模式驗簽（見 auth_verifiers）。

    本地模式的 email 僅供權限授予查詢與稽核欄位使用，非登入身分（無登入系統）；
    可用 env LOCAL_USER_EMAIL 指定（如需在單機環境測試 grants[email] 顆粒權限），
    未設則回通用佔位 email。
    """
    from app.core.permissions.deps import auth_config

    if str(auth_config().get("authProvider") or "local").lower() != "be2":
        return {"email": env.local_user_email or "local@kkday.internal"}

    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="未提供認證 token")
    from app.core.auth_verifiers import (
        get_verifier,  # 函式內 import 防循環（verifier 反向用 permissions.deps）
    )

    user = get_verifier().resolve_user(credentials.credentials)
    if not user:
        raise HTTPException(status_code=401, detail="token 無效或已過期")
    return user

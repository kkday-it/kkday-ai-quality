"""登入身分驗證 provider 化（authentication 層的可替換接縫）。

對齊權限層三件套模式（permissions/deps.get_provider）：`auth.config.json['authProvider']`
分流 local / be2，`get_current_user` 只經 `get_verifier().resolve_user(token)` 取使用者，
換 be2 登入時 router 與下游（user_settings / verdict_by / role_for）全不動。

- **LocalJwtVerifier**：現行自建 JWT（HS256·sub=user_id）——dev 與過渡期 production 用。
- **Be2TokenVerifier**：be2 中央 Auth Service accessToken（Cookie authToken 的 JWT）。
  身分解析＝方案 A：claims email → 以 email 查/建本地 users row（自動 provision·password_hash
  置空），`user_settings` 沿用本地 user_id 為鍵、業務表身分留痕（email）零遷移。

⚠️ Be2 驗簽待補（TODO·不臆造）：be2 系前端全部只做 payload base64 decode（kkday-auth-sdk-js
`parseClaims` 同）、真驗證在 Auth Service / api-gateway（Entry Config＋`x-kkday-auth-svc-status`）。
FastAPI 自驗 accessToken 的 server-to-server 契約（驗簽公鑰或 verify API）**須向 auth team 索取**
——接通前 `authProvider=be2` 僅 development 可用（production 啟用即拒），防未驗簽 token 混入正式環境。
"""

from __future__ import annotations

import base64
import json
import logging
import uuid
from typing import Protocol

from app.core import db
from app.core.config import is_production

_log = logging.getLogger(__name__)


class AuthVerifier(Protocol):
    """登入 verifier 介面：token → user dict（user_id/email…）或 None（無效）。"""

    def resolve_user(self, token: str) -> dict | None:  # pragma: no cover - Protocol
        ...


class LocalJwtVerifier:
    """自建 JWT（HS256·sub=user_id）→ users 表查使用者。現行預設路徑，行為與舊 get_current_user 等價。"""

    def resolve_user(self, token: str) -> dict | None:
        from app.core import auth  # 函式內 import：auth 於 get_current_user 反向依賴本模組

        user_id = auth.decode_access_token(token)
        if not user_id:
            return None
        return db.get_user_by_id(user_id)


class Be2TokenVerifier:
    """be2 Auth Service accessToken → email 自動 provision 本地 users row。

    claims 取 email（實測 be2 JWT 含 authOid/subAuthOid/platformId/exp；email 欄位名以
    接入時實際 token 為準——常見 `email`/`account`，兩者皆試）。exp 過期即拒。

    TODO(auth-team 契約)：簽章/撤銷驗證——取得 Auth Service 驗證 API 或 JWKS 後在此補上；
    目前僅 payload decode＋exp 檢查，故 production 禁用（get_verifier 硬閘）。
    """

    def resolve_user(self, token: str) -> dict | None:
        claims = _decode_jwt_payload_unverified(token)
        if not claims:
            return None
        # exp 檢查（epoch 秒；缺 exp 視為無效——be2 token 必帶）
        import time

        exp = claims.get("exp")
        if not isinstance(exp, (int, float)) or exp < time.time():
            return None
        email = str(claims.get("email") or claims.get("account") or "").strip().lower()
        if "@" not in email:
            return None
        user = db.get_user_by_email(email)
        if not user:
            # 自動 provision：be2 身分首登即建本地列（password_hash 空＝不可走 local 密碼登入），
            # user_settings 以此 user_id 為鍵、業務表留痕仍用 email——歷史資料零遷移（方案 A）。
            try:
                db.create_user(str(uuid.uuid4()), email, "")
                _log.info("be2 首登自動 provision 本地使用者：%s", email)
            except db.DuplicateEmailError:  # 併發首登 race：重查即可
                pass
            user = db.get_user_by_email(email)  # 重查完整列（create_user 回傳不含 password_hash）
        return user


def get_verifier() -> AuthVerifier:
    """依 auth.config.json['authProvider'] 選登入 verifier——換 be2 登入的**唯一後端分流點**。

    Raises:
        RuntimeError: production 選 be2 但驗簽契約未接（Be2TokenVerifier 尚無簽章驗證）——
            拒啟用防未驗簽 token 進正式環境；development 放行供接入前流程調試。
    """
    from app.core.permissions.deps import auth_config  # 共用單一 config 讀取器（含 reload 快取）

    name = str(auth_config().get("authProvider") or "local").lower()
    if name == "be2":
        if is_production():
            raise RuntimeError(
                "authProvider=be2 尚未接上 Auth Service 驗簽契約（見 auth_verifiers.py TODO），"
                "正式環境禁用；請先向 auth team 取得 server-to-server 驗證規格。"
            )
        return Be2TokenVerifier()
    return LocalJwtVerifier()


def _decode_jwt_payload_unverified(token: str) -> dict | None:
    """純 base64 解 JWT payload（不驗簽——對齊 kkday-auth-sdk-js parseClaims）；格式異常回 None。"""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        payload = parts[1] + "=" * (-len(parts[1]) % 4)  # base64url padding 補齊
        return json.loads(base64.urlsafe_b64decode(payload))
    except (ValueError, TypeError):
        return None

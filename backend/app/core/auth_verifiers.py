"""登入身分驗證 provider 化（authentication 層的可替換接縫）。

去帳戶系統（2026-07-22）：本地模式（authProvider=local）不再驗證任何 token——
`auth.get_current_user` 直接回固定身分，本模組僅在 `authProvider=be2` 時才介入。

- **Be2TokenVerifier**：be2 中央 Auth Service accessToken（Cookie authToken 的 JWT）。
  身分解析＝claims email 直接作為身分（無本地 users 表——身分僅供權限查詢
  `permissions.json` 的 `grants[email]` 與業務表稽核留痕，皆以 email 為鍵，
  不需落庫任何帳號列）；設定為全項目共享單例（見 core/settings.py）。

⚠️ Be2 驗簽待補（TODO·不臆造）：be2 系前端全部只做 payload base64 decode（kkday-auth-sdk-js
`parseClaims` 同）、真驗證在 Auth Service / api-gateway（Entry Config＋`x-kkday-auth-svc-status`）。
FastAPI 自驗 accessToken 的 server-to-server 契約（驗簽公鑰或 verify API）**須向 auth team 索取**
——接通前 `authProvider=be2` 僅 development 可用（production 啟用即拒），防未驗簽 token 混入正式環境。
"""

from __future__ import annotations

import base64
import json
from typing import Protocol

from app.core.config import is_production


class AuthVerifier(Protocol):
    """登入 verifier 介面：token → user dict（email…）或 None（無效）。"""

    def resolve_user(self, token: str) -> dict | None:  # pragma: no cover - Protocol
        ...


class Be2TokenVerifier:
    """be2 Auth Service accessToken → claims email 直接作為身分。

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
        return {"email": email}


def get_verifier() -> AuthVerifier:
    """be2 模式登入 verifier（唯一實作接縫）——production 未接妥驗簽契約即拒用。

    只在 `authProvider=be2` 時被呼叫（見 `auth.get_current_user`；local 模式不經過此函式，
    直接回固定身分）。日後若 be2 驗簽方式有變或新增其他 SSO，僅改本函式與其實作類別。

    Raises:
        RuntimeError: production 環境但驗簽契約未接（Be2TokenVerifier 尚無簽章驗證）——
            拒啟用防未驗簽 token 進正式環境；development 放行供接入前流程調試。
    """
    if is_production():
        raise RuntimeError(
            "authProvider=be2 尚未接上 Auth Service 驗簽契約（見 auth_verifiers.py TODO），"
            "正式環境禁用；請先向 auth team 取得 server-to-server 驗證規格。"
        )
    return Be2TokenVerifier()


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

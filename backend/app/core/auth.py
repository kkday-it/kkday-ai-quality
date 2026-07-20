"""帳號認證：bcrypt 密碼雜湊 + JWT 簽發/驗證 + FastAPI 認證依賴。

JWT secret 由環境變數 AIQ_JWT_SECRET 提供（經 config.env 集中讀取）；未設時用開發預設值
並記警告（正式環境務必設定）。
直接使用 bcrypt 套件（非 passlib）——passlib 1.7.4 與 bcrypt 5.x 不相容。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import env, is_production

_log = logging.getLogger(__name__)

_JWT_ALGORITHM = "HS256"
_TOKEN_TTL = timedelta(days=env.jwt_ttl_days)  # env JWT_TTL_DAYS（prod 可縮短）
_DEV_SECRET = "dev-insecure-secret-change-me"  # 僅開發 fallback，正式須設 AIQ_JWT_SECRET
_BCRYPT_MAX_BYTES = 72  # bcrypt 演算法上限，超過會拋 ValueError，故先截斷
_MIN_SECRET_BYTES = 32  # JWT secret 最低位元組數（HS256 弱 secret 可被暴力/彩虹表偽造）

# 啟動即檢查：非 development 環境的 AIQ_JWT_SECRET 必須存在且夠強（≥32 bytes）→ 否則拒絕啟動。
# 避免正式環境靜默用可預測的 dev secret、或用過短 secret 簽發可被偽造的 JWT（本模組被 main.py import，啟動即觸發）。
if is_production():
    _secret_val = (env.aiq_jwt_secret or "").strip()
    if not _secret_val:
        raise RuntimeError(
            f"APP_ENV={env.app_env} 為正式環境，必須設定 AIQ_JWT_SECRET；"
            "拒絕以開發預設 secret 啟動（JWT 可被偽造）。"
        )
    if len(_secret_val.encode("utf-8")) < _MIN_SECRET_BYTES:
        raise RuntimeError(
            f"AIQ_JWT_SECRET 過短（需 ≥{_MIN_SECRET_BYTES} bytes），弱 secret 易被偽造；"
            '生成：python -c "import secrets; print(secrets.token_urlsafe(32))"'
        )


def _secret() -> str:
    """取 JWT 簽名 secret。正式環境缺 secret 已在模組載入時拒啟動，此處僅 development 回 dev fallback。"""
    s = env.aiq_jwt_secret
    if not s:
        _log.warning(
            "AIQ_JWT_SECRET 未設定，使用開發預設 secret（僅限 development）；正式環境務必設定。"
        )
        return _DEV_SECRET
    return s


def hash_password(password: str) -> str:
    """bcrypt 雜湊密碼，回傳可存 DB 的字串。"""
    pw = password.encode("utf-8")[:_BCRYPT_MAX_BYTES]
    return bcrypt.hashpw(pw, bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """驗證明文密碼是否符合雜湊；任何格式異常一律視為不符。"""
    try:
        pw = password.encode("utf-8")[:_BCRYPT_MAX_BYTES]
        return bcrypt.checkpw(pw, password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_access_token(user_id: str) -> str:
    """簽發 JWT（sub=user_id，exp=7 天）。"""
    now = datetime.now(timezone.utc)
    payload = {"sub": user_id, "iat": now, "exp": now + _TOKEN_TTL}
    return jwt.encode(payload, _secret(), algorithm=_JWT_ALGORITHM)


def decode_access_token(token: str) -> str | None:
    """解碼 JWT，回傳 user_id（sub）；無效或過期回 None。"""
    try:
        payload = jwt.decode(token, _secret(), algorithms=[_JWT_ALGORITHM])
        sub = payload.get("sub")
        return sub if isinstance(sub, str) else None
    except jwt.PyJWTError:
        return None


_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict:
    """FastAPI 認證依賴：解析 Authorization: Bearer → 回傳 user dict；無效則 401。

    token 驗證經 auth_verifiers.get_verifier() 分流（auth.config.json authProvider：
    local=自建 JWT｜be2=Auth Service accessToken＋email 自動 provision）——換 be2 登入
    時本函式與全部 router 零改，唯一分流點在 auth_verifiers。
    """
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="未提供認證 token")
    from app.core.auth_verifiers import (
        get_verifier,  # 函式內 import 防循環（verifier 反向用本檔 decode）
    )

    user = get_verifier().resolve_user(credentials.credentials)
    if not user:
        raise HTTPException(status_code=401, detail="token 無效或已過期，或使用者不存在")
    user["role"] = role_for(user.get("email"))  # config 白名單即時派生（見檔末 RBAC 區）
    return user


# ── 輕量 RBAC（config 白名單驅動·零 migration）─────────────────────────────
# 角色每請求由 config/global/roles.json 即時派生（非 JWT claim）：改名單免重簽 token 即生效。
# 兩級：admin（規則發布 / 恢復默認）｜qc（判決 / 查看 / 上傳）。
_ROLES_CACHE: dict | None = None


def _roles_cfg() -> dict:
    """讀 roles.json（lazy 快取；檔缺/壞回空 → 全員 defaultRole，不阻斷登入）。"""
    global _ROLES_CACHE
    if _ROLES_CACHE is None:
        import json

        from app.core.paths import GLOBAL_DIR

        try:
            _ROLES_CACHE = json.loads((GLOBAL_DIR / "roles.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _log.warning("roles.json 缺失或格式錯誤，全員視為 defaultRole（qc）")
            _ROLES_CACHE = {}
    return _ROLES_CACHE


def reload_roles() -> None:
    """清 roles 快取（編輯 roles.json 後呼叫；或重啟 server）。"""
    global _ROLES_CACHE
    _ROLES_CACHE = None


def role_for(email: str | None) -> str:
    """email → 角色（admins 名單比對不分大小寫；其餘 defaultRole，預設 qc）。

    角色→具體 business-key 權限的映射與端點守衛由可替換權限框架負責
    （見 app/core/permissions：`require_permission` + `role_permissions.json`）——本函式僅派生角色。
    """
    cfg = _roles_cfg()
    admins = {str(e).strip().lower() for e in (cfg.get("admins") or [])}
    if email and email.strip().lower() in admins:
        return "admin"
    return str(cfg.get("defaultRole") or "qc")

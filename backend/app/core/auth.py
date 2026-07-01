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

from app.core import db
from app.core.config import env

_log = logging.getLogger(__name__)

_JWT_ALGORITHM = "HS256"
_TOKEN_TTL = timedelta(days=env.jwt_ttl_days)  # env JWT_TTL_DAYS（prod 可縮短）
_DEV_SECRET = "dev-insecure-secret-change-me"  # 僅開發 fallback，正式須設 AIQ_JWT_SECRET
_BCRYPT_MAX_BYTES = 72  # bcrypt 演算法上限，超過會拋 ValueError，故先截斷


def _secret() -> str:
    s = env.aiq_jwt_secret
    if not s:
        _log.warning("AIQ_JWT_SECRET 未設定，使用開發預設 secret；正式環境務必設定環境變數。")
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
    """FastAPI 認證依賴：解析 Authorization: Bearer → 回傳 user dict；無效則 401。"""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="未提供認證 token")
    user_id = decode_access_token(credentials.credentials)
    if not user_id:
        raise HTTPException(status_code=401, detail="token 無效或已過期")
    user = db.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=401, detail="使用者不存在")
    return user

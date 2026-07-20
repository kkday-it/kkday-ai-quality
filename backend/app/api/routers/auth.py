"""帳號系統端點（註冊 / 登入 / 當前使用者）；prefix 自帶 /api/auth。

自建帳號體系定位（be2-ready 架構）：**dev fallback**——production 正式路徑＝be2 SSO
（authProvider=be2，見 core/auth_verifiers.py）。be2 驗簽契約接通後：production 的
register/login 全退役（bootstrap admin 改 be2 角色綁定）、本檔僅供本地開發離線登入。
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core import auth, config, db
from app.core.errors import raise_api_error
from app.core.permissions import business_list_ttl_ms, get_provider

router = APIRouter()


class RegisterIn(BaseModel):
    email: str
    password: str


class LoginIn(BaseModel):
    email: str
    password: str


def _public_user(user: dict) -> dict:
    """去除 password_hash，只回傳可公開欄位。"""
    return {
        "user_id": user["user_id"],
        "email": user["email"],
        "created_at": user.get("created_at"),
        "role": user.get("role")
        or auth.role_for(user.get("email")),  # login 路徑 user 來自 DB 無 role
    }


def _register_allowed() -> bool:
    """自助註冊是否放行：顯式 AIQ_ALLOW_SELF_REGISTER 優先，未設則僅 development 放行。

    防生產環境任何人自助建帳號即取得 qc 角色全權（含 datapack.import 全庫覆寫）；
    prod 首次部署 bootstrap admin 時臨時設 true，建完帳號即移除（見 docker/README.md）。
    """
    if config.env.aiq_allow_self_register is not None:
        return config.env.aiq_allow_self_register
    return not config.is_production()


@router.post("/api/auth/register")
def register(body: RegisterIn) -> dict:
    """註冊新帳號 → 回 JWT + user。email 重複回 409；非放行環境回 403。"""
    if not _register_allowed():
        raise_api_error(
            "AUTH.REGISTER_DISABLED",
            f"目前環境（APP_ENV={config.env.app_env}）未開放自助註冊；"
            "設 AIQ_ALLOW_SELF_REGISTER=true 才可用。",
            status_code=403,
        )
    email = body.email.strip().lower()
    if "@" not in email or len(body.password) < config.env.min_password_length:
        raise_api_error(
            "AUTH.INVALID_CREDENTIALS_FORMAT",
            f"email 格式錯誤或密碼少於 {config.env.min_password_length} 碼",
            status_code=400,
        )
    try:
        user = db.create_user(str(uuid.uuid4()), email, auth.hash_password(body.password))
    except db.DuplicateEmailError:
        raise_api_error("AUTH.EMAIL_EXISTS", "此 email 已註冊", status_code=409)
    return {"token": auth.create_access_token(user["user_id"]), "user": _public_user(user)}


@router.post("/api/auth/login")
def login(body: LoginIn) -> dict:
    """登入 → 回 JWT + user。帳密錯誤回 401。"""
    email = body.email.strip().lower()
    user = db.get_user_by_email(email)
    if not user or not auth.verify_password(body.password, user["password_hash"] or ""):
        raise_api_error("AUTH.LOGIN_FAILED", "email 或密碼錯誤", status_code=401)
    return {"token": auth.create_access_token(user["user_id"]), "user": _public_user(user)}


@router.get("/api/auth/me")
def me(user: dict = Depends(auth.get_current_user)) -> dict:
    """回傳當前登入使用者。"""
    return _public_user(user)


@router.get("/api/auth/permissions")
def permissions(user: dict = Depends(auth.get_current_user)) -> dict:
    """回當前 user 的 business-key 權限清單（be2 `auth.business-list` 契約形狀 {value, ttl}）。

    前端存 localStorage 供 hasPermission / v-auth 使用；shape 現在即等於 be2（Confluence 佐證
    僅 value/ttl 兩欄，曾多回的 startTime 查無公司契約依據已移除——快取時間戳由前端自記），
    日後接 be2 中央 Auth SVC 時前端消費端（store/directive/guard）零改——只換 permission.api.ts 的來源。
    """
    value = sorted(get_provider().get_permissions(user))
    return {"value": value, "ttl": business_list_ttl_ms()}

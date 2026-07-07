"""帳號系統端點（註冊 / 登入 / 當前使用者）；prefix 自帶 /api/auth。"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core import auth, config, db

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


@router.post("/api/auth/register")
def register(body: RegisterIn) -> dict:
    """註冊新帳號 → 回 JWT + user。email 重複回 409。"""
    email = body.email.strip().lower()
    if "@" not in email or len(body.password) < config.env.min_password_length:
        raise HTTPException(
            status_code=400,
            detail=f"email 格式錯誤或密碼少於 {config.env.min_password_length} 碼",
        )
    try:
        user = db.create_user(str(uuid.uuid4()), email, auth.hash_password(body.password))
    except db.DuplicateEmailError:
        raise HTTPException(status_code=409, detail="此 email 已註冊") from None
    return {"token": auth.create_access_token(user["user_id"]), "user": _public_user(user)}


@router.post("/api/auth/login")
def login(body: LoginIn) -> dict:
    """登入 → 回 JWT + user。帳密錯誤回 401。"""
    email = body.email.strip().lower()
    user = db.get_user_by_email(email)
    if not user or not auth.verify_password(body.password, user["password_hash"] or ""):
        raise HTTPException(status_code=401, detail="email 或密碼錯誤")
    return {"token": auth.create_access_token(user["user_id"]), "user": _public_user(user)}


@router.get("/api/auth/me")
def me(user: dict = Depends(auth.get_current_user)) -> dict:
    """回傳當前登入使用者。"""
    return _public_user(user)

"""當前身分 + 權限清單端點；prefix 自帶 /api/auth。

去帳戶系統（2026-07-22）：本地模式無登入（register/login/登出/切換帳號已移除），
`auth.get_current_user` 回固定身分；production 正式路徑＝be2 SSO（authProvider=be2，
見 core/auth_verifiers.py）。本檔僅保留「查當前身分」與「查權限清單」兩個唯讀端點。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core import auth
from app.core.permissions import business_list_ttl_ms, get_provider

router = APIRouter()


@router.get("/api/auth/me")
def me(user: dict = Depends(auth.get_current_user)) -> dict:
    """回傳當前身分（本地模式為固定身分；be2 模式為 token 解析出的使用者）。"""
    return {"user_id": user.get("user_id"), "email": user.get("email")}


@router.get("/api/auth/permissions")
def permissions(user: dict = Depends(auth.get_current_user)) -> dict:
    """回當前 user 的 business-key 權限清單（be2 `auth.business-list` 契約形狀 {value, ttl}）。

    前端存 localStorage 供 hasPermission / v-auth 使用；shape 現在即等於 be2（Confluence 佐證
    僅 value/ttl 兩欄，曾多回的 startTime 查無公司契約依據已移除——快取時間戳由前端自記），
    日後接 be2 中央 Auth SVC 時前端消費端（store/directive/guard）零改——只換 permission.api.ts 的來源。
    """
    value = sorted(get_provider().get_permissions(user))
    return {"value": value, "ttl": business_list_ttl_ms()}

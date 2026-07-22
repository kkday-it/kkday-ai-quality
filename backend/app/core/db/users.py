"""帳號系統（users）+ per-user 設定（user_settings）持久化。"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import insert as sa_insert
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.db import tables as T


class DuplicateEmailError(Exception):
    """email 已存在（create_user 衝突）；上層轉 409。driver-agnostic，不洩漏底層例外型別。"""


def create_user(user_id: str, email: str, password_hash: str) -> dict:
    """建立使用者；email 重複拋 DuplicateEmailError（呼叫端轉 409）。回傳 user dict。"""
    created_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    stmt = sa_insert(T.users).values(
        user_id=user_id, email=email, password_hash=password_hash, created_at=created_at
    )
    try:
        with T.get_engine().begin() as c:
            c.execute(stmt)
    except IntegrityError as e:
        raise DuplicateEmailError(email) from e
    return {"user_id": user_id, "email": email, "created_at": created_at}


def get_user_by_email(email: str) -> dict | None:
    """以 email 取使用者（含 password_hash，供登入驗證）；無則 None。"""
    stmt = select(T.users).where(T.users.c.email == email)
    with T.get_engine().connect() as c:
        row = c.execute(stmt).mappings().first()
    return dict(row) if row else None


def get_user_by_id(user_id: str) -> dict | None:
    """以 user_id 取使用者；無則 None。"""
    stmt = select(T.users).where(T.users.c.user_id == user_id)
    with T.get_engine().connect() as c:
        row = c.execute(stmt).mappings().first()
    return dict(row) if row else None


def load_user_settings(user_id: str) -> dict | None:
    """讀某 user 的設定（完整 dict，含明文 token）；尚未存過則回 None（由上層套 _DEFAULT）。"""
    stmt = select(T.user_settings.c.data).where(T.user_settings.c.user_id == user_id)
    with T.get_engine().connect() as c:
        row = c.execute(stmt).first()
    if not row or not row[0]:
        return None
    try:
        return json.loads(row[0])
    except json.JSONDecodeError:
        return None


def list_user_ids_with_settings() -> list[str]:
    """列所有已存過設定的 user_id（qc_evidence 系統級憑證掃描用）。"""
    stmt = select(T.user_settings.c.user_id)
    with T.get_engine().connect() as c:
        return [r[0] for r in c.execute(stmt)]


def save_user_settings(user_id: str, data: dict) -> None:
    """覆寫某 user 的完整設定 dict（冪等：user_id 重複則覆蓋）。"""
    updated_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    values = {
        "user_id": user_id,
        "data": json.dumps(data, ensure_ascii=False),
        "updated_at": updated_at,
    }
    with T.get_engine().begin() as c:
        c.execute(T.upsert(T.user_settings, values, ["user_id"]))

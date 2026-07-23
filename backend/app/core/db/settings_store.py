"""全項目共享設定（settings 表·單例 row，key 固定 __global__，見 core/settings.py）持久化。"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.db import tables as T


def load_settings_row(key: str) -> dict | None:
    """讀設定 row（完整 dict，含 at-rest 密文機密）；尚未存過則回 None（由上層套 _DEFAULT）。"""
    stmt = select(T.settings.c.data).where(T.settings.c.key == key)
    with T.get_engine().connect() as c:
        row = c.execute(stmt).first()
    if not row or not row[0]:
        return None
    try:
        return json.loads(row[0])
    except json.JSONDecodeError:
        return None


def save_settings_row(key: str, data: dict) -> None:
    """覆寫設定 row 的完整 dict（冪等 upsert：key 重複則覆蓋）。"""
    updated_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    values = {
        "key": key,
        "data": json.dumps(data, ensure_ascii=False),
        "updated_at": updated_at,
    }
    with T.get_engine().begin() as c:
        c.execute(T.upsert(T.settings, values, ["key"]))

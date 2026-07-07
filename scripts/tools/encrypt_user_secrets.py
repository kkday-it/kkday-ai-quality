#!/usr/bin/env python3
"""user_settings 機密 at-rest 加密遷移：既有明文 provider_tokens / qc_passwords → Fernet 密文。

加解密實作在 backend/app/core/crypto.py（key＝backend/.env 的 AIQ_SECRET_KEY）；本檔掛 backend
上 sys.path 後逐 user 讀 → 轉 → 寫回。冪等（已加密列跳過）；--decrypt 供回滾（移除 key 前先轉回明文）。

用法：
  python scripts/tools/encrypt_user_secrets.py            # 明文 → 密文（需 .env 已設 AIQ_SECRET_KEY）
  python scripts/tools/encrypt_user_secrets.py --dry-run  # 只印將變更的 user / 欄位數，不寫
  python scripts/tools/encrypt_user_secrets.py --decrypt  # 密文 → 明文（回滾用）
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# repo 根：scripts/tools/encrypt_user_secrets.py → parents[2]；backend 掛上 sys.path 才能 import app.*
_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "backend"))

from app.core import crypto, db  # noqa: E402

_SECRET_KEYS = ("provider_tokens", "qc_passwords")


def _list_user_ids() -> list[str]:
    """撈 user_settings 全部 user_id（僅遷移已存過設定的 user）。"""
    from sqlalchemy import select

    from app.core.db import tables as T

    with T.get_engine().connect() as c:
        return [r[0] for r in c.execute(select(T.user_settings.c.user_id))]


def _transform(data: dict, decrypt: bool) -> int:
    """就地轉換機密 map，回實際變更的值數（已是目標型態者不計）。"""
    changed = 0
    for key in _SECRET_KEYS:
        m = data.get(key) or {}
        for k, v in m.items():
            new = crypto.decrypt_secret(v) if decrypt else crypto.encrypt_secret(v)
            if new != v:
                m[k] = new
                changed += 1
        data[key] = m
    return changed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--decrypt", action="store_true", help="密文轉回明文（回滾）")
    ap.add_argument("--dry-run", action="store_true", help="只印變更統計，不寫 DB")
    args = ap.parse_args()

    if not args.decrypt and crypto._fernet() is None:
        print("❌ AIQ_SECRET_KEY 未設定（backend/.env），加密遷移中止")
        return 1

    total = 0
    for uid in _list_user_ids():
        data = db.load_user_settings(uid)
        if not data:
            continue
        changed = _transform(data, decrypt=args.decrypt)
        if changed:
            if not args.dry_run:
                db.save_user_settings(uid, data)
            print(f"{'[dry-run] ' if args.dry_run else ''}{uid}: {changed} 個機密值已轉換")
            total += changed
    mode = "解密" if args.decrypt else "加密"
    print(f"完成：{mode}轉換 {total} 個機密值")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

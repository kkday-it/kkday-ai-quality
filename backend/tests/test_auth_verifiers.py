"""登入 verifier 分流（auth_verifiers）測試。

鎖：①authProvider 分流與 production be2 硬閘 ②Be2 token decode（exp/email）③email 自動
provision＋併發 race ④local 路徑與舊行為等價（既有 test_api_endpoints 已覆蓋端到端）。
"""

from __future__ import annotations

import base64
import json
import time

import pytest

from app.core import auth_verifiers as av


def _fake_be2_token(claims: dict) -> str:
    """組一個未簽名 JWT（header.payload.sig 形狀；Be2TokenVerifier 只 decode payload）。"""
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")  # noqa: E731
    return f"{enc({'alg': 'none'})}.{enc(claims)}.x"


def test_provider_dispatch_local_default(monkeypatch) -> None:
    monkeypatch.setattr("app.core.permissions.deps.auth_config", lambda: {"authProvider": "local"})
    assert isinstance(av.get_verifier(), av.LocalJwtVerifier)


def test_be2_blocked_in_production(monkeypatch) -> None:
    """production 選 be2（驗簽契約未接）→ 拒啟用（防未驗簽 token 進正式環境）。"""
    monkeypatch.setattr("app.core.permissions.deps.auth_config", lambda: {"authProvider": "be2"})
    monkeypatch.setattr(av, "is_production", lambda: True)
    with pytest.raises(RuntimeError, match="auth team"):
        av.get_verifier()


def test_be2_allowed_in_development(monkeypatch) -> None:
    monkeypatch.setattr("app.core.permissions.deps.auth_config", lambda: {"authProvider": "be2"})
    monkeypatch.setattr(av, "is_production", lambda: False)
    assert isinstance(av.get_verifier(), av.Be2TokenVerifier)


def test_be2_rejects_expired_or_bad_token(temp_db) -> None:
    v = av.Be2TokenVerifier()
    assert v.resolve_user("not-a-jwt") is None
    assert v.resolve_user(_fake_be2_token({"email": "a@kkday.com"})) is None  # 缺 exp
    assert (
        v.resolve_user(_fake_be2_token({"email": "a@kkday.com", "exp": time.time() - 10})) is None
    )
    assert v.resolve_user(_fake_be2_token({"exp": time.time() + 600})) is None  # 缺 email


def test_be2_auto_provision_and_reuse(temp_db) -> None:
    """be2 首登自動建本地 users row（方案 A）；再登沿用同一 user_id（user_settings 鍵穩定）。"""
    v = av.Be2TokenVerifier()
    tok = _fake_be2_token({"email": "Be2.User@KKday.com", "exp": time.time() + 600})
    u1 = v.resolve_user(tok)
    assert u1 and u1["email"] == "be2.user@kkday.com"  # normalize 小寫
    assert u1["password_hash"] == ""  # 不可走 local 密碼登入
    u2 = v.resolve_user(tok)
    assert u2["user_id"] == u1["user_id"]  # 再登不重建

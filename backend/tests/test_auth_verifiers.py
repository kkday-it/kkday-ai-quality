"""登入 verifier 分流（auth_verifiers）測試。

鎖：①production be2 硬閘 ②Be2 token decode（exp/email）③email normalize 直回身分（無 DB）。
local 模式已不經過 get_verifier（見 auth.get_current_user 直接回固定身分，
test_permissions.py::test_local_mode_never_401_unauthenticated 已覆蓋端到端）。
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


def test_be2_blocked_in_production(monkeypatch) -> None:
    """production 呼叫 get_verifier（驗簽契約未接）→ 拒啟用（防未驗簽 token 進正式環境）。

    get_verifier 只在 auth.get_current_user 已判定 authProvider=be2 時才被呼叫（見該函式），
    故本函式自身不再讀 authProvider，只憑 is_production() 決定是否放行。
    """
    monkeypatch.setattr(av, "is_production", lambda: True)
    with pytest.raises(RuntimeError, match="auth team"):
        av.get_verifier()


def test_be2_allowed_in_development(monkeypatch) -> None:
    monkeypatch.setattr(av, "is_production", lambda: False)
    assert isinstance(av.get_verifier(), av.Be2TokenVerifier)


def test_be2_rejects_expired_or_bad_token() -> None:
    v = av.Be2TokenVerifier()
    assert v.resolve_user("not-a-jwt") is None
    assert v.resolve_user(_fake_be2_token({"email": "a@kkday.com"})) is None  # 缺 exp
    assert (
        v.resolve_user(_fake_be2_token({"email": "a@kkday.com", "exp": time.time() - 10})) is None
    )
    assert v.resolve_user(_fake_be2_token({"exp": time.time() + 600})) is None  # 缺 email


def test_be2_resolves_email_identity() -> None:
    """be2 token → claims email normalize 小寫直接作為身分（無本地 users 表，不落庫）。"""
    v = av.Be2TokenVerifier()
    tok = _fake_be2_token({"email": "Be2.User@KKday.com", "exp": time.time() + 600})
    assert v.resolve_user(tok) == {"email": "be2.user@kkday.com"}

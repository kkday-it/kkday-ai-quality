"""機密 at-rest 加密（app/core/crypto.py + settings.py 落庫邊界）測試。

覆蓋：round-trip、密文不含原文、未設 key 明文直通、settings save→DB 落密文→load 還原明文、
舊明文列直通（漸進遷移）、key 不符回空值。
"""

from __future__ import annotations

import json

import pytest

from app.core import crypto
from app.core.config import env


@pytest.fixture
def with_key(monkeypatch):
    """設定測試用 AIQ_SECRET_KEY 並清 Fernet 快取；結束還原無 key 狀態。"""
    monkeypatch.setattr(env, "aiq_secret_key", "test-passphrase-42")
    crypto._fernet.cache_clear()
    yield
    crypto._fernet.cache_clear()


def test_round_trip_and_ciphertext_opaque(with_key):
    plain = "sk-test123-secret"
    enc = crypto.encrypt_secret(plain)
    assert enc.startswith(crypto.ENC_PREFIX)
    assert plain not in enc  # 密文不含原文
    assert crypto.decrypt_secret(enc) == plain
    # 冪等：再加密不會二次包裹
    assert crypto.encrypt_secret(enc) == enc


def test_no_key_passthrough(monkeypatch):
    monkeypatch.setattr(env, "aiq_secret_key", None)
    crypto._fernet.cache_clear()
    assert crypto.encrypt_secret("plain-token") == "plain-token"
    assert crypto.decrypt_secret("plain-token") == "plain-token"
    crypto._fernet.cache_clear()


def test_wrong_key_returns_empty(with_key, monkeypatch):
    enc = crypto.encrypt_secret("sk-abc")
    monkeypatch.setattr(env, "aiq_secret_key", "another-key")
    crypto._fernet.cache_clear()
    assert crypto.decrypt_secret(enc) == ""  # key 不符 → 空值，不外洩密文


def test_settings_at_rest_encrypted(temp_db, with_key):
    """save_settings 落庫為密文；load_settings 還原明文；DB 原始 JSON 無明文。"""
    from app.core import db
    from app.core import settings as app_settings

    app_settings.save_settings(
        {
            "llm_connections": {"openai": {"base_url": ""}},
            "llm_tokens": {"openai": "sk-live-9999xyz"},
        },
    )
    # DB 原始列：密文、無原文
    raw_row = db.load_settings_row(app_settings.GLOBAL_SETTINGS_KEY)
    stored_tok = raw_row["llm_tokens"]["openai"]
    assert stored_tok.startswith(crypto.ENC_PREFIX)
    assert "sk-live-9999xyz" not in json.dumps(raw_row)
    # load 邊界還原明文
    loaded = app_settings.load_settings()
    assert loaded["llm_tokens"]["openai"] == "sk-live-9999xyz"
    # masked() 遮罩後不含原文也不含密文
    masked = app_settings.masked()
    assert "sk-live-9999xyz" not in json.dumps(masked)


def test_settings_legacy_plaintext_row_readable(temp_db, with_key):
    """既有明文列（未跑遷移腳本前）load 直通可讀，再次 save 即升級為密文。"""
    from app.core import db
    from app.core import settings as app_settings

    blank = app_settings._blank_settings()
    blank["llm_connections"] = {"openai": {"base_url": ""}}
    blank["llm_tokens"] = {"openai": "sk-old-plain"}
    db.save_settings_row(app_settings.GLOBAL_SETTINGS_KEY, blank)  # 直落明文，模擬加密上線前的舊列

    loaded = app_settings.load_settings()
    assert loaded["llm_tokens"]["openai"] == "sk-old-plain"

    app_settings.save_settings({})  # 任意 save 觸發重落庫 → 加密
    raw_row = db.load_settings_row(app_settings.GLOBAL_SETTINGS_KEY)
    assert raw_row["llm_tokens"]["openai"].startswith(crypto.ENC_PREFIX)

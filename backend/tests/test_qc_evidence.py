"""qc_evidence（訂單佐證查詢層）單元測試——全打樁，絕不連 production。

S1 範圍：PII allow-list 斷言（投影 SQL + payload key 掃描）、resolve_credentials 分支、
get_evidence 降級分支（未配置/空單號）。快取/single-flight/熔斷 測試隨 S3 擴充。
"""

from __future__ import annotations

import pytest

from app.core.db import qc_evidence


# ── PII 防線 ──────────────────────────────────────────────────────────────────────────────
def test_projection_sql_contains_no_pii_columns():
    """投影 SQL（allow-list 主防線）不得出現任何個資欄位關鍵字。"""
    for sql in qc_evidence.ALL_PROJECTION_SQL:
        low = sql.lower()
        for kw in qc_evidence.PII_KEYWORDS:
            assert kw not in low, f"PII 欄位 {kw} 出現在投影 SQL：{sql[:80]}..."


def test_projection_sql_registry_covers_module_sql_constants():
    """模組內所有 _SQL_* 常數都必須登記進 ALL_PROJECTION_SQL（防新增點查漏掃）。"""
    sql_consts = {
        name: val
        for name, val in vars(qc_evidence).items()
        if name.startswith("_SQL_") and isinstance(val, str)
    }
    for name, val in sql_consts.items():
        assert val in qc_evidence.ALL_PROJECTION_SQL, f"{name} 未登記進 ALL_PROJECTION_SQL"


def test_assert_no_pii_keys_catches_leak():
    """payload key 含個資關鍵字必須 fail-loud（第二道防線有效性）。"""
    with pytest.raises(ValueError, match="PII"):
        qc_evidence.assert_no_pii_keys({"order": {"contact_email": "x@y.z"}})
    with pytest.raises(ValueError, match="PII"):
        qc_evidence.assert_no_pii_keys([{"nested": [{"member_uuid": "abc"}]}])


def test_assert_no_pii_keys_ignores_values():
    """只掃 key 不掃 value：文案 value 含 email 字樣屬正常內容，不可誤殺。"""
    qc_evidence.assert_no_pii_keys({"desc": "請聯繫 contact_email 客服信箱"})  # 不拋即通過


# ── resolve_credentials ──────────────────────────────────────────────────────────────────
def _settings_with(env: str, *, password: str = "pw") -> dict:
    """組一份含單一 active QC config 的 user settings 樣板。"""
    return {
        "active_qc_config_id": "cfg1",
        "qc_configs": [{"id": "cfg1", "env": env, "host": "h.example", "port": 5432, "user": "u1"}],
        "qc_passwords": {"cfg1": password},
    }


def test_resolve_credentials_production_fallback():
    """active config 為 production 且有密碼 → 解出連線參數（dbname 取 evidence.json）。"""
    creds = qc_evidence.resolve_credentials(_settings_with("production"))
    assert creds is not None
    assert creds["host"] == "h.example"
    assert creds["user"] == "u1"
    assert creds["password"] == "pw"
    assert creds["dbname"]  # 來自 evidence.json db.dbname


def test_resolve_credentials_rejects_non_production():
    """只有 sit config → None（佐證只准連 production，不誤連測試庫）。"""
    assert qc_evidence.resolve_credentials(_settings_with("sit")) is None


def test_resolve_credentials_inactive_production_still_resolves():
    """active 是 sit、另有未啟用的 production config → 仍解析 production（不強迫切 active）。"""
    s = {
        "active_qc_config_id": "sit1",
        "qc_configs": [
            {"id": "sit1", "env": "sit", "host": "sit.example", "port": 5432, "user": "u1"},
            {
                "id": "prod1",
                "env": "production",
                "host": "prod.example",
                "port": 5432,
                "user": "u1",
            },
        ],
        "qc_passwords": {"sit1": "pw-sit", "prod1": "pw-prod"},
    }
    creds = qc_evidence.resolve_credentials(s)
    assert creds is not None
    assert creds["host"] == "prod.example"
    assert creds["password"] == "pw-prod"


def test_resolve_credentials_prefers_active_production():
    """多個 production config 時 active 優先（deterministic 選取）。"""
    s = {
        "active_qc_config_id": "prod2",
        "qc_configs": [
            {"id": "prod1", "env": "production", "host": "a.example", "port": 5432, "user": "u1"},
            {"id": "prod2", "env": "production", "host": "b.example", "port": 5432, "user": "u1"},
        ],
        "qc_passwords": {"prod1": "pw-a", "prod2": "pw-b"},
    }
    creds = qc_evidence.resolve_credentials(s)
    assert creds is not None
    assert creds["host"] == "b.example"


def test_resolve_credentials_rejects_missing_password():
    """無密碼 → None（不半殘連線）。"""
    assert qc_evidence.resolve_credentials(_settings_with("production", password="")) is None


def test_resolve_credentials_env_service_account_first(monkeypatch):
    """env 服務帳號存在時優先於 per-user config（R17 切換點）。"""
    from app.core.config import env as app_env

    monkeypatch.setattr(app_env, "evidence_db_host", "svc.host")
    monkeypatch.setattr(app_env, "evidence_db_user", "svc_user")
    monkeypatch.setattr(app_env, "evidence_db_password", "svc_pw")
    creds = qc_evidence.resolve_credentials(_settings_with("production"))
    assert creds is not None
    assert creds["host"] == "svc.host"
    assert creds["user"] == "svc_user"


# ── get_evidence 降級分支（不觸網）──────────────────────────────────────────────────────
def test_get_evidence_degrades_without_credentials():
    """未注入憑證（contextvar None）→ degraded_unavailable，不拋錯。"""
    qc_evidence.set_current(None)
    r = qc_evidence.get_evidence("123")
    assert r.status == "degraded_unavailable"
    assert r.data is None


def test_get_evidence_no_order_oid():
    """空單號 → no_order_oid（與查詢失敗語義分離）。"""
    qc_evidence.set_current(
        {"host": "h", "port": 1, "user": "u", "password": "p", "dbname": "d", "schema": "public"}
    )
    try:
        assert qc_evidence.get_evidence("").status == "no_order_oid"
        assert qc_evidence.get_evidence(None).status == "no_order_oid"
    finally:
        qc_evidence.set_current(None)


def test_get_evidence_disabled(monkeypatch):
    """evidence.json enabled=false → degraded_unavailable（一鍵停用開關有效）。"""
    monkeypatch.setattr(qc_evidence, "_cfg_cache", {**qc_evidence._cfg(), "enabled": False})
    r = qc_evidence.get_evidence("123")
    assert r.status == "degraded_unavailable"

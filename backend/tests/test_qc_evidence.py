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


def test_pii_guard_scope_exempts_product_content():
    """防線只掃自組區塊：商品目錄 JSONB 的 spec 標籤（如 passportNo）不得誤殺（Phase A 實測教訓）。"""
    assert "product_info" not in qc_evidence.PII_GUARD_SECTIONS
    assert "item_info" not in qc_evidence.PII_GUARD_SECTIONS
    assert "package_info" not in qc_evidence.PII_GUARD_SECTIONS
    assert set(qc_evidence.PII_GUARD_SECTIONS) == {"order_summary", "supplier_info", "meta"}
    # 模擬 get_evidence 的作用域裁剪：商品內容含 passportNo spec 標籤 → 通過
    data = {
        "order_summary": {"order_mid": "26KK1", "prod_oid": 11},
        "supplier_info": {"supplier_name": "s"},
        "meta": {"source": "qc-snapshot"},
        "item_info": {"item_setting": [{"spec": {"passportNo": {"required": True}}}]},
    }
    qc_evidence.assert_no_pii_keys({k: data.get(k) for k in qc_evidence.PII_GUARD_SECTIONS})


# ── resolve_credentials ──────────────────────────────────────────────────────────────────
def _settings_with(env: str, *, password: str = "pw") -> dict:
    """組一份含單一環境 QC 連線的全域 settings 樣板（A schema：qc_connections keyed by env）。"""
    return {
        "qc_connections": {env: {"host": "h.example", "port": 5432, "user": "u1"}},
        "qc_passwords": {env: password},
    }


def test_resolve_credentials_production_fallback():
    """production 環境連線且有密碼 → 解出連線參數（dbname 取 evidence.json）。"""
    creds = qc_evidence.resolve_credentials(_settings_with("production"))
    assert creds is not None
    assert creds["host"] == "h.example"
    assert creds["user"] == "u1"
    assert creds["password"] == "pw"
    assert creds["dbname"]  # 來自 evidence.json db.dbname


def test_resolve_credentials_rejects_non_production():
    """只有 sit 環境連線 → None（佐證只准連 production，不誤連測試庫）。"""
    assert qc_evidence.resolve_credentials(_settings_with("sit")) is None


def test_resolve_credentials_ignores_non_production_even_with_production_present():
    """同時配 sit 與 production 連線 → 仍只解析 production（環境各自獨立 key，不互相干擾）。"""
    s = {
        "qc_connections": {
            "sit": {"host": "sit.example", "port": 5432, "user": "u1"},
            "production": {"host": "prod.example", "port": 5432, "user": "u1"},
        },
        "qc_passwords": {"sit": "pw-sit", "production": "pw-prod"},
    }
    creds = qc_evidence.resolve_credentials(s)
    assert creds is not None
    assert creds["host"] == "prod.example"
    assert creds["password"] == "pw-prod"


def test_resolve_credentials_rejects_missing_password():
    """無密碼 → None（不半殘連線）。"""
    assert qc_evidence.resolve_credentials(_settings_with("production", password="")) is None


def test_resolve_credentials_any_reads_global(monkeypatch):
    """去隔離後：resolve_credentials_any(None) 直讀全局配置的 production QC（不再掃庫）。"""
    from app.core import settings as app_settings

    monkeypatch.setattr(
        app_settings, "load_settings", lambda: _settings_with("production", password="pw-global")
    )
    creds = qc_evidence.resolve_credentials_any(None)
    assert creds is not None
    assert creds["host"] == "h.example"
    assert creds["password"] == "pw-global"


def test_resolve_credentials_any_prefers_passed_settings(monkeypatch):
    """傳入 s（有 production 憑證）時優先用 s，不再讀全局（呼叫端已持有全局那份）。"""
    from app.core import settings as app_settings

    def _boom():
        raise AssertionError("should not read global when passed settings resolve")

    monkeypatch.setattr(app_settings, "load_settings", _boom)
    creds = qc_evidence.resolve_credentials_any(_settings_with("production", password="own"))
    assert creds is not None
    assert creds["password"] == "own"


def test_resolve_credentials_any_none_when_no_config_anywhere(monkeypatch):
    """全項目無 production QC 憑證 → None（端點降級 degraded_unavailable）。"""
    from app.core import settings as app_settings

    monkeypatch.setattr(app_settings, "load_settings", lambda: _settings_with("sit"))
    assert qc_evidence.resolve_credentials_any(None) is None


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


# ── S3：single-flight / 熔斷 / 兩級快取 ─────────────────────────────────────────────────
@pytest.fixture()
def _isolated_state(monkeypatch, temp_db):
    """隔離 S3 模組級狀態：engine 導向測試庫（conftest temp_db 建表+清空+測後還原），重置熔斷計數。

    快取讀寫走 PG evidence_snapshot 表——不依賴 temp_db 會寫進 dev 庫（踩過）。
    """
    monkeypatch.setattr(qc_evidence, "_breaker_fails", 0)
    monkeypatch.setattr(qc_evidence, "_breaker_opened_at", 0.0)
    yield
    qc_evidence.set_current(None)


def test_singleflight_merges_concurrent_calls(_isolated_state):
    """同 key 併發 → 底層 fn 只執行一次，所有呼叫者共享同一結果。"""
    import threading as th

    calls = []
    gate = th.Event()

    def slow_fn():
        calls.append(1)
        gate.wait(timeout=2)
        return "value"

    results = []
    threads = [
        th.Thread(target=lambda: results.append(qc_evidence._singleflight("k1", slow_fn)))
        for _ in range(5)
    ]
    for t in threads:
        t.start()
    import time as _t

    _t.sleep(0.2)  # 等 follower 全部掛上 future
    gate.set()
    for t in threads:
        t.join(timeout=5)
    assert len(calls) == 1, "底層 fn 應只執行一次"
    assert results == ["value"] * 5


def test_singleflight_propagates_leader_exception(_isolated_state):
    """leader 拋例外 → follower 原樣收到（不靜默吞掉）。"""
    import threading as th

    gate = th.Event()

    def bad_fn():
        gate.wait(timeout=2)
        raise RuntimeError("boom")

    errors = []

    def _follower():
        try:
            qc_evidence._singleflight("k2", bad_fn)
        except RuntimeError as e:
            errors.append(str(e))

    threads = [th.Thread(target=_follower) for _ in range(3)]
    for t in threads:
        t.start()
    import time as _t

    _t.sleep(0.2)
    gate.set()
    for t in threads:
        t.join(timeout=5)
    assert errors == ["boom"] * 3


def test_breaker_opens_after_threshold_and_half_opens(_isolated_state, monkeypatch):
    """連續失敗達閾值 → BreakerOpen；冷卻過後放行一次探測（half-open）；成功清零。"""
    threshold = int((qc_evidence._cfg().get("db") or {}).get("breaker_threshold", 5))
    for _ in range(threshold):
        qc_evidence._breaker_record(ok=False)
    with pytest.raises(qc_evidence.BreakerOpen):
        qc_evidence._breaker_allow()
    # 冷卻已過（把開啟時刻撥回過去）→ 放行探測
    monkeypatch.setattr(qc_evidence, "_breaker_opened_at", 1.0)
    qc_evidence._breaker_allow()  # 不拋＝half-open 放行
    qc_evidence._breaker_record(ok=True)
    assert qc_evidence._breaker_fails == 0


def _sample_snapshot(oid: str) -> dict:
    """合成扁平欄位 dict（鍵＝`_SNAPSHOT_VALUE_COLUMNS`；stub `_fetch_full_snapshot` 用，不觸網）。"""
    return {
        "order_mid": "26KK1",
        "order_status": "GO",
        "price_pay": 1.0,
        "lang_code": "zh-tw",
        "crt_dt": "2026-01-01T00:00:00",
        "prod_oid": 11,
        "prod_version": 22,
        "pkg_oid": 33,
        "item_oid": 44,
        "supplier_oid": 55,
        "lst_dt_go": "2026-02-01T00:00:00",
        "timezone": "Asia/Taipei",
        "pkg_name": "pkg",
        "prod_desc": "desc",
        "supplier_name": "s",
        "supplier_order_handler": "KKDAY",
        "supplier_msg_handler": "KKDAY",
        "product_summary": {"category": "M01"},
        "product_desc_module": None,
        "item_lang": [],
        "item_setting": None,
        "package_lang": None,
        "package_setting": None,
        "package_policy": None,
        "package_module_setting": None,
    }


def _stub_snapshot(monkeypatch, counter):
    """打樁 `_fetch_full_snapshot`（計數底層實查次數，不觸網）。"""
    monkeypatch.setattr(
        qc_evidence,
        "_fetch_full_snapshot",
        lambda creds, oid: (counter.__setitem__("n", counter["n"] + 1) or _sample_snapshot(oid)),
    )


def test_get_evidence_single_row_cache_hit(_isolated_state, monkeypatch):
    """首查 fetched（實查一次）；重查 cache_hit（DB 零觸碰）；meta.cache_hit 旗標正確。"""
    counter = {"n": 0}
    _stub_snapshot(monkeypatch, counter)
    qc_evidence.set_current(
        {"host": "h", "port": 1, "user": "u", "password": "p", "dbname": "d", "schema": "public"}
    )

    r1 = qc_evidence.get_evidence("123")
    assert r1.status == "fetched"
    assert counter == {"n": 1}
    assert r1.data["order_summary"]["order_oid"] == 123
    assert r1.data["order_summary"]["prod_oid"] == 11
    assert r1.data["meta"]["cache_hit"] is False

    r2 = qc_evidence.get_evidence("123")
    assert r2.status == "cache_hit"
    assert counter == {"n": 1}, "快取命中不得再觸 DB"
    assert r2.data["meta"]["cache_hit"] is True


def test_get_evidence_per_order_no_cross_order_sharing(_isolated_state, monkeypatch):
    """一訂單一列：不同 order_oid 各自實查一次（不再有商品版本跨訂單共用快取）。"""
    counter = {"n": 0}
    _stub_snapshot(monkeypatch, counter)
    qc_evidence.set_current(
        {"host": "h", "port": 1, "user": "u", "password": "p", "dbname": "d", "schema": "public"}
    )

    qc_evidence.get_evidence("123")
    r2 = qc_evidence.get_evidence("456")  # 不同 order_oid → 全量重查
    assert r2.status == "fetched"
    assert counter["n"] == 2, "不同訂單各自實查一次（無跨訂單共用）"


def test_get_evidence_not_found_not_cached(_isolated_state, monkeypatch):
    """查無此單不落快取：資料補上後重查應能查到（避免暫態 not_found 長駐）。"""
    state = {"exists": False}
    monkeypatch.setattr(
        qc_evidence,
        "_fetch_full_snapshot",
        lambda creds, oid: _sample_snapshot(oid) if state["exists"] else None,
    )
    qc_evidence.set_current(
        {"host": "h", "port": 1, "user": "u", "password": "p", "dbname": "d", "schema": "public"}
    )
    assert qc_evidence.get_evidence("999").status == "not_found"
    state["exists"] = True
    assert qc_evidence.get_evidence("999").status == "fetched"


def test_ttl_knob_reads_config():
    """訂單快照 TTL 讀 config（一訂單一列的單一 TTL，小時級——R6 易變資料短 TTL）。"""
    assert qc_evidence._order_ttl_s() == 6 * 3600

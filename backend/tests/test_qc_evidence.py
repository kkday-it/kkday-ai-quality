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
    assert "product_lang" not in qc_evidence.PII_GUARD_SECTIONS
    assert "product_setting" not in qc_evidence.PII_GUARD_SECTIONS
    assert set(qc_evidence.PII_GUARD_SECTIONS) == {"order", "supplier", "meta"}
    # 模擬 get_evidence 的作用域裁剪：商品內容含 passportNo spec 標籤 → 通過
    data = {
        "order": {"order_mid": "26KK1"},
        "supplier": {"supplier_name": "s"},
        "meta": {"lang": "zh-tw"},
        "product_setting": {"item_summary": [{"spec": {"passportNo": {"required": True}}}]},
    }
    qc_evidence.assert_no_pii_keys({k: data.get(k) for k in qc_evidence.PII_GUARD_SECTIONS})


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


def _stub_bundles(monkeypatch, counters):
    """打樁三個 DB 實查 bundle（計數呼叫次數，不觸網）。"""
    monkeypatch.setattr(
        qc_evidence,
        "_fetch_order_bundle",
        lambda creds, oid: (
            counters.__setitem__("order", counters["order"] + 1)
            or {
                "order_oid": int(oid),
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
                "package_name": "pkg",
                "prod_desc": "desc",
            }
        ),
    )
    monkeypatch.setattr(
        qc_evidence,
        "_fetch_product_bundle",
        lambda creds, order, lang: (
            counters.__setitem__("prod", counters["prod"] + 1)
            or {
                "product_lang": {"item_summary": []},
                "product_setting": {"category": "M01"},
                "pkg_basic": None,
                "module_setting": None,
            }
        ),
    )
    monkeypatch.setattr(
        qc_evidence,
        "_fetch_supplier_bundle",
        lambda creds, sup_oid: (
            counters.__setitem__("sup", counters["sup"] + 1)
            or {"supplier_name": "s", "order_handler": "KKDAY", "msg_handler": "KKDAY"}
        ),
    )


def test_get_evidence_two_level_cache_hit(_isolated_state, monkeypatch):
    """首查 fetched（實查一次）；重查 cache_hit（DB 零觸碰）；meta.cache 旗標正確。"""
    counters = {"order": 0, "prod": 0, "sup": 0}
    _stub_bundles(monkeypatch, counters)
    qc_evidence.set_current(
        {"host": "h", "port": 1, "user": "u", "password": "p", "dbname": "d", "schema": "public"}
    )

    r1 = qc_evidence.get_evidence("123")
    assert r1.status == "fetched"
    assert counters == {"order": 1, "prod": 1, "sup": 1}

    r2 = qc_evidence.get_evidence("123")
    assert r2.status == "cache_hit"
    assert counters == {"order": 1, "prod": 1, "sup": 1}, "快取命中不得再觸 DB"
    assert r2.data["meta"]["cache"] == {"order": True, "product": True, "supplier": True}


def test_get_evidence_product_cache_shared_across_orders(_isolated_state, monkeypatch):
    """同商品版本的另一張訂單：order 級實查、商品級/供應商級快取共用（兩級去重語義）。"""
    counters = {"order": 0, "prod": 0, "sup": 0}
    _stub_bundles(monkeypatch, counters)
    qc_evidence.set_current(
        {"host": "h", "port": 1, "user": "u", "password": "p", "dbname": "d", "schema": "public"}
    )

    qc_evidence.get_evidence("123")
    r2 = qc_evidence.get_evidence("456")  # 不同 order_oid、stub 回同 prod/ver/pkg
    assert r2.status == "fetched"  # order 級是新查
    assert counters["order"] == 2
    assert counters["prod"] == 1, "同商品版本應命中商品級快取"
    assert counters["sup"] == 1


def test_get_evidence_not_found_not_cached(_isolated_state, monkeypatch):
    """查無此單不落快取：資料補上後重查應能查到（避免暫態 not_found 長駐）。"""
    state = {"exists": False}
    monkeypatch.setattr(
        qc_evidence,
        "_fetch_order_bundle",
        lambda creds, oid: ({"order_oid": 1} | _minimal_order()) if state["exists"] else None,
    )
    qc_evidence.set_current(
        {"host": "h", "port": 1, "user": "u", "password": "p", "dbname": "d", "schema": "public"}
    )
    assert qc_evidence.get_evidence("999").status == "not_found"
    state["exists"] = True
    _stub_rest(monkeypatch)
    assert qc_evidence.get_evidence("999").status == "fetched"


def _minimal_order() -> dict:
    """not_found 測試用最小 order dict。"""
    return {
        "order_mid": "26KK9",
        "order_status": "GO",
        "price_pay": 1.0,
        "lang_code": "zh-tw",
        "crt_dt": "t",
        "prod_oid": 1,
        "prod_version": 2,
        "pkg_oid": 3,
        "item_oid": 4,
        "supplier_oid": 5,
        "lst_dt_go": "t",
        "timezone": "tz",
        "package_name": "p",
        "prod_desc": "d",
    }


def _stub_rest(monkeypatch):
    """補 stub 商品/供應商 bundle（not_found 測試後半用）。"""
    monkeypatch.setattr(
        qc_evidence,
        "_fetch_product_bundle",
        lambda creds, order, lang: {
            "product_lang": None,
            "product_setting": None,
            "pkg_basic": None,
            "module_setting": None,
        },
    )
    monkeypatch.setattr(
        qc_evidence, "_fetch_supplier_bundle", lambda creds, sup_oid: {"supplier_name": "s"}
    )


def test_ttl_knobs_read_config():
    """兩級 TTL 讀 config：order 短（小時級）、product 長（天級）——R6 差異化政策。"""
    assert qc_evidence._order_ttl_s() == 6 * 3600
    assert qc_evidence._product_ttl_s() == 30 * 86400

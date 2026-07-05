"""OpenFeature 判決閾值介面（core.flags）測試：provider 解析 confidence_tiers + 預設回退 + 型別邊界。

不碰 DB（monkeypatch judgment 配置讀取）；驗證 threshold() 走 OpenFeature 標準且反映 DB active 值、
reload() 清 cache、provider 僅處理 float（其餘型別回 default·不越權）。
"""

from __future__ import annotations

import pytest

from app.core import flags
from app.core.db import _shared


@pytest.fixture(autouse=True)
def _reset_flags_cache():
    """每測試前後清 flags 閾值 cache（避免 monkeypatch 值跨測試洩漏）。"""
    flags.reload()
    yield
    flags.reload()


def test_threshold_reads_confidence_tiers(monkeypatch) -> None:
    """threshold() 反映 judgment 配置 confidence_tiers（DB active 值，經 provider）。"""
    monkeypatch.setattr(
        _shared,
        "read_judgment_config",
        lambda: {"confidence_tiers": {"auto_accept": 0.9, "jury_low": 0.4, "jury_high": 0.6}},
    )
    flags.reload()
    assert flags.threshold("auto_accept", 0.8) == 0.9
    assert flags.threshold("jury_low", 0.5) == 0.4
    assert flags.threshold("jury_high", 0.7) == 0.6


def test_threshold_default_for_unknown_tier(monkeypatch) -> None:
    """配置缺該 tier → 回呼叫端 default（provider DEFAULT reason）。"""
    monkeypatch.setattr(_shared, "read_judgment_config", lambda: {"confidence_tiers": {"auto_accept": 0.8}})
    flags.reload()
    assert flags.threshold("nonexistent", 0.42) == 0.42


def test_threshold_builtin_default_when_no_tiers(monkeypatch) -> None:
    """配置無 confidence_tiers → 內建預設（auto_accept=0.8），不至於 0。"""
    monkeypatch.setattr(_shared, "read_judgment_config", lambda: {})
    flags.reload()
    assert flags.threshold("auto_accept", 0.0) == 0.8


def test_provider_float_only(monkeypatch) -> None:
    """provider 僅 float 職責：boolean/string 旗標回 default（不越權）。"""
    monkeypatch.setattr(_shared, "read_judgment_config", lambda: {"confidence_tiers": {"auto_accept": 0.9}})
    flags.reload()
    c = flags._client()
    assert c.get_boolean_value("judge.auto_accept", True) is True
    assert c.get_string_value("judge.auto_accept", "x") == "x"

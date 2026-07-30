"""pricing.py：花費估算 + 配置壞掉時的診斷留痕。

`_load()` 對缺檔/壞檔是刻意 fail-soft（回空表、不中斷初判），但這代表所有花費會靜默顯示 $0
且落進 `llm_usage` 表——沒有 log 就沒有線索可查「怎麼全部免費」。這裡鎖住「壞檔時仍要留一筆
warning」這個行為，不讓 fail-soft 退化成沒有痕跡。
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from app.core.judge_config import pricing


@pytest.fixture(autouse=True)
def _reset_pricing_cache():
    """`_load()` 有模組級快取，測試前後都清掉，避免互相汙染。"""
    pricing._table = None
    pricing._default = {"input": 0.0, "output": 0.0}
    yield
    pricing._table = None
    pricing._default = {"input": 0.0, "output": 0.0}


def test_price_for_known_model_reads_real_config() -> None:
    """正常路徑：讀真實 llm_model.json，已知 model 給出非零單價。"""
    p = pricing.price_for("gpt-5.4-mini")
    assert p["input"] > 0
    assert p["output"] > 0


def test_load_failure_falls_back_to_zero_and_logs_warning(monkeypatch, caplog) -> None:
    """壞檔/缺檔 → 花費回退全 0（既有 fail-soft 行為不變），但必須留一筆 warning 可查。"""
    monkeypatch.setattr(
        Path, "read_text", lambda self, *a, **k: (_ for _ in ()).throw(OSError("boom"))
    )
    with caplog.at_level(logging.WARNING, logger="app.core.judge_config.pricing"):
        result = pricing.price_for("gpt-5.4-mini")
    assert result == {"input": 0.0, "output": 0.0}
    assert any("讀取/解析失敗" in r.getMessage() for r in caplog.records)


def test_load_failure_cost_usd_is_zero_not_exception(monkeypatch) -> None:
    """配置壞掉時 cost_usd 仍要能算（回 0），不能讓計價路徑本身炸掉、拖累初判主流程。"""
    monkeypatch.setattr(
        Path, "read_text", lambda self, *a, **k: (_ for _ in ()).throw(OSError("boom"))
    )
    assert pricing.cost_usd("gpt-5.4-mini", 1000, 500) == 0.0

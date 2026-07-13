"""單支 Prompt 評測核心（prompt_eval.py）純函式測試：對照/指標，不需 DB/LLM。

`domain_of`/`prompt_id_of` 鎖死 C-N ↔ prompt_id ↔ 域機器值三方對照（樹退役後改自
`prompt_source` 檔名尾綴派生，非 DB 樹 tree[0].domain——回歸測試防再次踩壞）。
`compute_domain_metrics`/`compute_polarity_metrics` 為與 I/O 解耦的純函式，逐一鎖數值計算。
"""

from __future__ import annotations

import pytest

from app.judge import prompt_eval


# ─────────────────────────── prompt 對照 ───────────────────────────
def test_prompt_id_of_maps_polarity_and_domains() -> None:
    """polarity → POLARITY_ID；C-N → 對應 prompt_id；未知拋 ValueError。"""
    assert prompt_eval.prompt_id_of("polarity") == "00_polarity"
    assert prompt_eval.prompt_id_of("C-3") == "03_C-3_supplier"
    with pytest.raises(ValueError, match="未知 prompt"):
        prompt_eval.prompt_id_of("C-9")


def test_domain_of_derives_from_prompt_filename() -> None:
    """C-N → 域機器值：改自 prompt_source 檔名尾綴派生（非已退役的 DB 樹 tree[0].domain）。"""
    assert prompt_eval.domain_of("C-1") == "content"
    assert prompt_eval.domain_of("C-2") == "quality"
    assert prompt_eval.domain_of("C-3") == "supplier"
    assert prompt_eval.domain_of("C-4") == "platform"
    assert prompt_eval.domain_of("C-5") == "service"
    assert prompt_eval.domain_of("C-6") == "customer"


# ─────────────────────────── 指標（純函式）───────────────────────────
def test_compute_domain_metrics_rates() -> None:
    """primary 一致率 / 棄權正確率 / 命中率 / 多報率——逐筆手算對照。"""
    records = [
        # 命中且 primary 對
        {"ref_l2s": ["C-3-1"], "ref_primary": "C-3-1", "pack_l2s": ["C-3-1"]},
        # 命中但 primary 錯（pack 排序第一條非 ref_primary）
        {"ref_l2s": ["C-3-1"], "ref_primary": "C-3-1", "pack_l2s": ["C-3-2"]},
        # 棄權正確（ref 無、pack 亦無）
        {"ref_l2s": [], "ref_primary": None, "pack_l2s": []},
        # 棄權錯誤（ref 無，pack 誤報）——同時也算多報（pack 長度 1 > ref 長度 0）
        {"ref_l2s": [], "ref_primary": None, "pack_l2s": ["C-3-1"]},
    ]
    m = prompt_eval.compute_domain_metrics(records)
    assert m["n"] == 4
    assert m["primary_match_rate"] == 0.5  # 2 筆有 primary，1 筆對
    assert m["abstain_correct_rate"] == 0.5  # 2 筆該棄權，1 筆正確棄權
    assert m["hit_rate"] == 1.0  # 2 筆該命中，2 筆皆非空
    assert m["over_report_rate"] == 0.25  # 4 筆中 1 筆 pack 條數 > ref 條數


def test_compute_domain_metrics_empty_returns_none_rates() -> None:
    """空記錄：n=0，各 rate 因分母為 0 回 None（非除以零錯誤）。"""
    m = prompt_eval.compute_domain_metrics([])
    assert m["n"] == 0
    assert m["primary_match_rate"] is None
    assert m["abstain_correct_rate"] is None
    assert m["hit_rate"] is None


def test_compute_polarity_metrics_match_rates() -> None:
    """polarity/sentiment 一致率各自獨立計算。"""
    records = [
        {"polarity": "negative", "sentiment": 1, "pack_polarity": "negative", "pack_sentiment": 1},
        {"polarity": "neutral", "sentiment": 3, "pack_polarity": "negative", "pack_sentiment": 3},
        {"polarity": "positive", "sentiment": 5, "pack_polarity": "positive", "pack_sentiment": 4},
    ]
    m = prompt_eval.compute_polarity_metrics(records)
    assert m["n"] == 3
    assert m["polarity_match_rate"] == round(2 / 3, 3)
    assert m["sentiment_match_rate"] == round(2 / 3, 3)


# ─────────────────────────── stub 模式拒跑（防假結果）───────────────────────────
def test_run_eval_rejects_stub_mode(monkeypatch) -> None:
    """stub（無 LLM token）時 run_eval 拒跑，避免產出誤導性假指標。"""
    from app.judge.llm import client

    monkeypatch.setattr(client, "is_stub", lambda: True)
    with pytest.raises(ValueError, match="stub 模式"):
        prompt_eval.run_eval("C-3", 10)


def test_classify_one_rejects_stub_mode(monkeypatch) -> None:
    """stub 模式時 classify_one 拒跑（單條 dry-run 分類同樣禁假結果）。"""
    from app.judge.llm import client

    monkeypatch.setattr(client, "is_stub", lambda: True)
    with pytest.raises(ValueError, match="stub 模式"):
        prompt_eval.classify_one("product_reviews", "r1")

"""單支 Prompt 評測的純指標函式測試（app.judge.prompt_eval.compute_*_metrics）。

指標計算刻意與 LLM/DB I/O 解耦成純函式（後端 SSOT，UI「測試」端點與 CLI eval_prompt_single 共用），
於此鎖定行為：指標定義改動即紅。
"""

from __future__ import annotations

from app.judge.prompt_eval import compute_domain_metrics, compute_polarity_metrics


def test_compute_domain_metrics_primary_hit_abstain_over():
    """primary 一致 / 命中 / 棄權 / 多報四指標逐一驗證。"""
    records = [
        # 本域有 primary，pack 首碼同 → primary_match + hit
        {"ref_l2s": ["C-3-1"], "ref_primary": "C-3-1", "pack_l2s": ["C-3-1"]},
        # 本域有 primary，pack 首碼不同 → hit 但非 primary_match
        {"ref_l2s": ["C-3-2"], "ref_primary": "C-3-2", "pack_l2s": ["C-3-9"]},
        # 他域（ref 空），pack 亦空 → abstain_ok
        {"ref_l2s": [], "ref_primary": None, "pack_l2s": []},
        # 他域（ref 空），pack 非空 → abstain 錯 + 多報
        {"ref_l2s": [], "ref_primary": None, "pack_l2s": ["C-3-1"]},
    ]
    m = compute_domain_metrics(records)
    assert m["n"] == 4
    assert m["primary_match_rate"] == 0.5  # 2 primary_total, 1 match
    assert m["hit_rate"] == 1.0  # 2 hit_total, 2 non-empty
    assert m["abstain_correct_rate"] == 0.5  # 2 abstain_total, 1 空
    assert m["over_report_rate"] == 0.25  # 1/4 pack 條數 > ref


def test_compute_domain_metrics_empty_yields_none_rates():
    """空紀錄集：所有分母為 0 → rate 皆 None（不除零）。"""
    m = compute_domain_metrics([])
    assert m["n"] == 0
    assert m["primary_match_rate"] is None
    assert m["hit_rate"] is None
    assert m["abstain_correct_rate"] is None


def test_compute_polarity_metrics():
    """polarity 一致率 + sentiment 一致率。"""
    records = [
        {"polarity": "negative", "sentiment": 2, "pack_polarity": "negative", "pack_sentiment": 2},
        {"polarity": "neutral", "sentiment": 3, "pack_polarity": "neutral", "pack_sentiment": 4},
        {"polarity": "positive", "sentiment": 5, "pack_polarity": "negative", "pack_sentiment": 1},
    ]
    m = compute_polarity_metrics(records)
    assert m["n"] == 3
    assert m["polarity_match_rate"] == round(2 / 3, 3)  # 2/3 polarity 對
    assert m["sentiment_match_rate"] == round(1 / 3, 3)  # 只有第一筆 sentiment 對

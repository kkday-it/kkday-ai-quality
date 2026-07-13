"""單支 Prompt 評測 harness 的純指標函式測試（_compute_domain_metrics / _compute_polarity_metrics）。

scripts/tools/eval_prompt_single.py 的指標計算刻意與 LLM/DB I/O 解耦成純函式，於此鎖定行為：
指標定義改動即紅。以檔案路徑載入（scripts/ 非 package，不入 sys.path 汙染其他測試）。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_EVAL_PATH = Path(__file__).resolve().parents[2] / "scripts" / "tools" / "eval_prompt_single.py"


def _load_eval():
    spec = importlib.util.spec_from_file_location("eval_prompt_single", _EVAL_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_compute_domain_metrics_primary_hit_abstain_over():
    """primary 一致 / 命中 / 棄權 / 多報四指標逐一驗證。"""
    ev = _load_eval()
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
    m = ev._compute_domain_metrics(records)
    assert m["n"] == 4
    assert m["primary_match_rate"] == 0.5  # 2 primary_total, 1 match
    assert m["hit_rate"] == 1.0  # 2 hit_total, 2 non-empty
    assert m["abstain_correct_rate"] == 0.5  # 2 abstain_total, 1 空
    assert m["over_report_rate"] == 0.25  # 1/4 pack 條數 > ref


def test_compute_domain_metrics_empty_yields_none_rates():
    """空紀錄集：所有分母為 0 → rate 皆 None（不除零）。"""
    ev = _load_eval()
    m = ev._compute_domain_metrics([])
    assert m["n"] == 0
    assert m["primary_match_rate"] is None
    assert m["hit_rate"] is None
    assert m["abstain_correct_rate"] is None


def test_compute_polarity_metrics():
    """polarity 一致率 + sentiment 一致率。"""
    ev = _load_eval()
    records = [
        {"polarity": "negative", "sentiment": 2, "pack_polarity": "negative", "pack_sentiment": 2},
        {"polarity": "neutral", "sentiment": 3, "pack_polarity": "neutral", "pack_sentiment": 4},
        {"polarity": "positive", "sentiment": 5, "pack_polarity": "negative", "pack_sentiment": 1},
    ]
    m = ev._compute_polarity_metrics(records)
    assert m["n"] == 3
    assert m["polarity_match_rate"] == round(2 / 3, 3)  # 2/3 polarity 對
    assert m["sentiment_match_rate"] == round(1 / 3, 3)  # 只有第一筆 sentiment 對

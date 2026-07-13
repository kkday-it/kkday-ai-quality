"""切分：pair 不跨 split、可複現、無泄漏、重複 id 拒絕（PRD §9/§19）。"""

from __future__ import annotations

import build_dataset as bd
import common
import pytest
import schemas as S


def _cases(n_pairs: int = 6, n_single: int = 10) -> list[S.CandidateCase]:
    out: list[S.CandidateCase] = []
    for i in range(n_pairs):
        pid = f"P{i}"
        out.append(
            S.CandidateCase(
                case_id=f"pair-{i}-a",
                domain_under_test="C-1",
                layer=2,
                text=f"頁面沒寫幾點集合{i}",
                input_polarity="negative",
                expected_domain="true",
                expected_l2_codes=["C-1-4"],
                expected_evidence_quotes=[f"沒寫幾點集合{i}"],
                case_family="contrast_pair",
                expression_variant="direct",
                difficulty="hard",
                language="zh-tw",
                boundary_with="C-3",
                contrast_pair_id=pid,
                contrast_key="集合說明",
            )
        )
        out.append(
            S.CandidateCase(
                case_id=f"pair-{i}-b",
                domain_under_test="C-1",
                layer=2,
                text=f"頁面寫清楚但現場遲到{i}",
                input_polarity="negative",
                expected_domain="false",
                case_family="contrast_pair",
                expression_variant="direct",
                difficulty="hard",
                language="zh-tw",
                boundary_with="C-3",
                contrast_pair_id=pid,
                contrast_key="集合說明",
            )
        )
    for j in range(n_single):
        out.append(
            S.CandidateCase(
                case_id=f"s-{j}",
                domain_under_test="C-1",
                layer=1,
                text=f"頁面沒說明時長{j}",
                input_polarity="negative",
                expected_domain="true",
                expected_l2_codes=["C-1-2"],
                expected_evidence_quotes=[f"沒說明時長{j}"],
                case_family="rule_unit",
                expression_variant="direct",
                difficulty="easy",
                language="zh-tw",
            )
        )
    return out


def test_pairs_never_split_across_dev_holdout():
    dev, ho = bd.split_dev_holdout(_cases(), seed=42)
    dev_pairs = {c.contrast_pair_id for c in dev if c.contrast_pair_id}
    ho_pairs = {c.contrast_pair_id for c in ho if c.contrast_pair_id}
    assert not (dev_pairs & ho_pairs)
    # 每個 pair 兩側都在同一側
    from collections import Counter

    for split in (dev, ho):
        cnt = Counter(c.contrast_pair_id for c in split if c.contrast_pair_id)
        assert all(v == 2 for v in cnt.values())


def test_split_reproducible_same_seed():
    a = bd.split_dev_holdout(_cases(), seed=42)
    b = bd.split_dev_holdout(_cases(), seed=42)
    assert [c.case_id for c in a[0]] == [c.case_id for c in b[0]]
    assert [c.case_id for c in a[1]] == [c.case_id for c in b[1]]


def test_no_leak_passes_and_detects():
    dev, ho = bd.split_dev_holdout(_cases(), seed=7)
    checks = bd.assert_no_leak(dev, ho)
    assert checks == {"case_id": "pass", "exact_text": "pass", "contrast_pair": "pass"}
    # 人為製造 id 泄漏 → 應 raise
    with pytest.raises(ValueError):
        bd.assert_no_leak(dev, ho + [dev[0]])


def test_split_ratio_roughly_70_30():
    dev, ho = bd.split_dev_holdout(_cases(n_pairs=20, n_single=60), seed=42)
    total = len(dev) + len(ho)
    assert 0.6 <= len(dev) / total <= 0.8  # 分層+pair 綁定下允許波動


def test_duplicate_case_id_rejected(tmp_path):
    p = tmp_path / "cands.jsonl"
    rows = [c.model_dump() for c in _cases(n_pairs=0, n_single=2)]
    rows.append(rows[0])  # 重複
    common.write_jsonl(p, rows)
    with pytest.raises(ValueError, match="重複 case_id"):
        bd.load_candidates(str(p))

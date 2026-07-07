"""DSPy 判決引擎鷹架測試（mock 可測部分；不需 LLM key / 不碰真 LM）。

驗 Signature 結構、metric exact-match、DspyJudge 組合邏輯（注入假 predict + 控制業務語義）、
compile 優雅 skip。需 dspy（`.[dspy]` extra，未裝則整檔 skip）。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("dspy")

from app.judge import dspy_engine as D  # noqa: E402
from app.judge import prejudge  # noqa: E402

_ATTR_CONTENT = {
    "l1_domain_code": "content",
    "l1_label": "商品內容",
    "l2_code": "L2",
    "l2_label": "描述",
    "l3_code": "L3",
    "l3_label": "不符",
    "confidence": 0.9,
    "raw_confidence": 0.9,
    "evidence_quote": "描述不符",
    "l3_candidates": [],
    "evidence_capped": False,
}
_ATTR_ABSTAIN = {
    "l1_domain_code": "",
    "l1_label": "",
    "l2_code": "",
    "l2_label": "",
    "l3_code": "",
    "l3_label": "",
    "confidence": 0.4,
    "raw_confidence": 0.4,
    "evidence_quote": "",
    "l3_candidates": [],
    "evidence_capped": False,
}


def _item() -> dict:
    return {"source": "product_reviews", "source_id": "R1", "comment": "測試評論內容"}


def test_signatures_structure() -> None:
    assert list(D.PolarityJudge.input_fields) == ["review"]
    assert list(D.PolarityJudge.output_fields) == ["polarity"]
    assert set(D.AttributionJudge.input_fields) == {"review", "catalog"}
    assert set(D.AttributionJudge.output_fields) == {"l3_code", "confidence", "evidence_quote"}


def test_attribution_metric_exact_match() -> None:
    got_content = [SimpleNamespace(l1_domain_code="content")]
    assert D.attribution_metric(SimpleNamespace(l1_code="content"), got_content) is True
    assert D.attribution_metric(SimpleNamespace(l1_code="supplier"), got_content) is False
    assert D.attribution_metric(SimpleNamespace(l1_code=""), got_content) is False  # 無 gold
    assert (
        D.attribution_metric(
            SimpleNamespace(l1_code="content"), [SimpleNamespace(l1_domain_code="")]
        )
        is False
    )


def test_dspy_judge_positive_non_issue() -> None:
    """極性判正向 → 單一非問題 finding（不歸因，不觸 attribute）。"""
    j = D.DspyJudge()
    j.polarity = lambda review: SimpleNamespace(polarity="positive")
    out = j.forward(_item())
    assert len(out) == 1
    assert out[0].polarity == "positive"
    assert out[0].judgment_stage == "judged"


def test_dspy_judge_negative_abstain_pending_data(monkeypatch) -> None:
    """負向但業務語義判 abstain（無域）→ 未歸因 pending_data + needs_review。"""
    monkeypatch.setattr(prejudge, "_finalize_attr", lambda *a, **k: dict(_ATTR_ABSTAIN))
    j = D.DspyJudge()
    j.polarity = lambda review: SimpleNamespace(polarity="negative")
    j.attribute = lambda review, catalog: SimpleNamespace(
        l3_code="", confidence=0.4, evidence_quote=""
    )
    out = j.forward(_item())
    assert len(out) == 1
    f = out[0]
    assert f.polarity == "negative"
    assert f.judgment_stage == "pending_data"
    assert f.needs_review is True


def test_dspy_judge_negative_attributed(monkeypatch) -> None:
    """負向 + 業務語義歸到 content 域 → 歸因 finding（DSPy 分類 + prejudge 業務語義整合）。"""
    monkeypatch.setattr(prejudge, "_finalize_attr", lambda *a, **k: dict(_ATTR_CONTENT))
    j = D.DspyJudge()
    j.polarity = lambda review: SimpleNamespace(polarity="negative")
    j.attribute = lambda review, catalog: SimpleNamespace(
        l3_code="L3", confidence=0.9, evidence_quote="描述不符"
    )
    out = j.forward(_item())
    assert len(out) == 1
    assert out[0].polarity == "negative"
    assert out[0].l1_domain_code == "content"


def test_compile_skips_without_labels_or_key() -> None:
    """無標註 / DB 不可達 / 無 key → 優雅 skip（不拋、不需 LLM）。"""
    rep = D.compile_and_persist()
    assert rep["status"] == "skipped"
    assert rep["reason"] in ("insufficient_labels", "db_unavailable", "no_llm_key")

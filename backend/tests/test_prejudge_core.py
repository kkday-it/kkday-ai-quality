"""判決核心（prejudge）確定性行為測試——建立引擎行為基準線（此前 659 行零測試）。

分兩層，皆不需 LLM key（stub）/ 不碰 DB：
- **純 helper**：信心分層邊界、階段派生、證據封頂、寬鬆 float 解析。
- **stub 管線**：`to_findings` 在 stub 模式的確定性啟發式（純好評略過 / 極性閘門 / 負向未歸因 pending_data）。

config 相關讀取（閾值 / 旋鈕 / 負向詞）一律 monkeypatch 固定，使斷言與 config 漂移解耦。
此基準線亦為未來換 DSPy 引擎（Phase 3）的行為對照。
"""

from __future__ import annotations

import pytest

from app.judge import prejudge

_FIXED_TIERS = {"auto_accept": 0.8, "jury_low": 0.5, "jury_high": 0.7}
_FIXED_CFG = {
    "neg_keywords": ["退款", "差", "誤導"],
    "enable_stage0_skip": True,
    "stage0_max_comment_len": 8,
    "max_attributions": 2,
}


@pytest.fixture
def fixed_config(monkeypatch):
    """固定閾值 + prejudge 旋鈕（與 config 漂移解耦）。"""
    monkeypatch.setattr(prejudge, "_tiers", lambda: dict(_FIXED_TIERS))
    monkeypatch.setattr(prejudge, "_prejudge_cfg", lambda: dict(_FIXED_CFG))


# ── 純 helper ──────────────────────────────────────────────────────────
def test_tier_for_boundaries(fixed_config) -> None:
    """信心分層邊界：>=auto_accept 採信 / <jury_low 人審 / 之間 評審。"""
    assert prejudge._tier_for(0.9) == "auto_accept"
    assert prejudge._tier_for(0.8) == "auto_accept"  # 邊界含
    assert prejudge._tier_for(0.79) == "jury"
    assert prejudge._tier_for(0.5) == "jury"  # 邊界（非 < jury_low）
    assert prejudge._tier_for(0.49) == "needs_review"
    assert prejudge._tier_for(0.0) == "needs_review"


def test_derive_stage_all_branches() -> None:
    """判決階段派生五分支。"""
    assert prejudge._derive_stage("unknown", "", "jury", False) == "insufficient"
    assert prejudge._derive_stage("positive", "", "auto_accept", False) == "judged"
    assert prejudge._derive_stage("neutral", "", "jury", False) == "judged"
    # 負向：無 L3 或 evidence-cap → pending_data
    assert prejudge._derive_stage("negative", "", "auto_accept", False) == "pending_data"
    assert prejudge._derive_stage("negative", "L3-1", "auto_accept", True) == "pending_data"
    # 負向 + 有 L3 + 未 cap：高信心 judged、否則 pending_review
    assert prejudge._derive_stage("negative", "L3-1", "auto_accept", False) == "judged"
    assert prejudge._derive_stage("negative", "L3-1", "jury", False) == "pending_review"
    assert prejudge._derive_stage("negative", "L3-1", "needs_review", False) == "pending_review"


def test_evidence_capped_supplier_needs_order() -> None:
    """證據封頂觸發：供應商域缺 order_oid（raw 內亦查）；其餘域 / 有訂單不觸發。"""
    assert prejudge._evidence_capped("supplier", {}) is True
    assert prejudge._evidence_capped("supplier", {"order_oid": "O1"}) is False
    assert prejudge._evidence_capped("supplier", {"raw": {"order_oid": "O1"}}) is False
    assert prejudge._evidence_capped("content", {}) is False


def test_evidence_cap_limits_supplier_without_order(fixed_config) -> None:
    """供應商缺訂單 → 封頂至 jury_high-0.01；有訂單 / 其他域 → 原值。"""
    assert prejudge._evidence_cap("supplier", {}, 0.95) == pytest.approx(0.69)
    assert prejudge._evidence_cap("supplier", {"order_oid": "O1"}, 0.95) == 0.95
    assert prejudge._evidence_cap("content", {}, 0.95) == 0.95


def test_as_float_lenient_parse_and_clip() -> None:
    """寬鬆 float：可解析夾 [0,1]；不可解析回 default（default 不夾）。"""
    assert prejudge._as_float("0.7") == 0.7
    assert prejudge._as_float(1.5) == 1.0  # 夾上限
    assert prejudge._as_float(-2) == 0.0  # 夾下限
    assert prejudge._as_float(None) == 0.0  # TypeError → default
    assert prejudge._as_float("bad", 0.3) == 0.3  # ValueError → default


def test_has_neg_kw(fixed_config) -> None:
    """負向關鍵詞偵測（含任一即真）。"""
    assert prejudge._has_neg_kw("我要退款") is True
    assert prejudge._has_neg_kw("服務很差") is True
    assert prejudge._has_neg_kw("很滿意推薦") is False


def test_stub_polarity_heuristic(fixed_config) -> None:
    """stub 極性：rating≤2 負 / ≥4 正 / 中間看負向詞 / 無詞有字 unknown / 無字 neutral。"""
    pol = prejudge._stub_polarity
    assert pol({"rating": 1}, "隨便") == "negative"
    assert pol({"rating": 2}, "隨便") == "negative"
    assert pol({"rating": 5}, "隨便") == "positive"
    assert pol({"rating": 4}, "隨便") == "positive"
    assert pol({"rating": 3}, "要退款") == "negative"  # 中間 + 負向詞
    assert pol({"rating": 3}, "普通") == "unknown"  # 中間 + 無負向詞 + 有字
    assert pol({}, "誤導消費者") == "negative"  # 無 rating 靠負向詞
    assert pol({}, "") == "neutral"  # 無 rating 無字


def test_skip0_pure_good_review(fixed_config) -> None:
    """Stage0 零 LLM 略過：rating=5 + 短評 + 無負向詞 才略過。"""
    assert prejudge._skip0({"rating": 5}, "讚") is True
    assert prejudge._skip0({"rating": 5}, "這個商品真的非常好用大推") is False  # 過長
    assert prejudge._skip0({"rating": 4}, "讚") is False  # 非滿分
    assert prejudge._skip0({"rating": 5}, "退款") is False  # 含負向詞


# ── stub 管線行為（to_findings） ────────────────────────────────────────
@pytest.fixture
def stub_engine(monkeypatch, fixed_config):
    """強制 stub 模式（不論環境是否有 OPENAI_API_KEY），使 to_findings 走確定性啟發式。"""
    monkeypatch.setattr(prejudge.client, "is_stub", lambda: True)


def _item(rating: int | None, comment: str, **extra) -> dict:
    base = {"source": "product_reviews", "source_id": "R1", "comment": comment, "prod_oid": "P1"}
    if rating is not None:
        base["rating"] = rating
    base.update(extra)
    return base


def test_to_findings_pure_good_review_non_issue(stub_engine) -> None:
    """純好評（rating=5 短評）→ 單一非問題正向 finding（Stage0 略過，不歸因）。"""
    out = prejudge.to_findings(_item(5, "讚"), model="gpt-5-nano")
    assert len(out) == 1
    f = out[0]
    assert f.polarity == "positive"
    assert f.judgment_stage == "judged"
    assert f.dimension == "non_content"
    assert f.finding_id == "fd_product_reviews_R1"


def test_to_findings_positive_non_issue(stub_engine) -> None:
    """rating≥4 長評無負向 → 正向非問題（Stage1 極性閘門收尾）。"""
    out = prejudge.to_findings(_item(5, "整體體驗很好值得推薦給朋友"), model="gpt-5-nano")
    assert len(out) == 1 and out[0].polarity == "positive"


def test_to_findings_negative_unattributed_pending_data(stub_engine) -> None:
    """負向（stub 無法歸因）→ 單一未歸因 finding：pending_data + needs_review。"""
    out = prejudge.to_findings(_item(1, "服務很差要退款"), model="gpt-5-nano")
    assert len(out) == 1
    f = out[0]
    assert f.polarity == "negative"
    assert f.judgment_stage == "pending_data"
    assert f.confidence_tier == "needs_review"
    assert f.needs_review is True


def test_to_findings_unknown_polarity_insufficient(stub_engine) -> None:
    """中間評分 + 無負向詞 + 有字 → 傾向不明 → insufficient（資訊不足）。"""
    out = prejudge.to_findings(_item(3, "普通"), model="gpt-5-nano")
    assert len(out) == 1
    f = out[0]
    assert f.polarity == "unknown"
    assert f.judgment_stage == "insufficient"

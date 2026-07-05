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


# ── 歸因處理正確性（_evidence_grounded / _finalize_attr）───────────────────
# 這批鎖「LLM 原始輸出 → 淨化 attr」的確定性邏輯——證據接地防編造、abstain 回填、證據封頂——
# 是判決核心的正確性關鍵，也是未來換 DSPy 引擎須逐位對齊的行為。

# 假 L3 節點解析（避開 ai_judge config 依賴，使斷言確定）。
_L3_NODES = {
    "L3-content": {"l1_domain_code": "content", "l1_label": "商品內容", "l2_code": "L2c", "l2_label": "描述", "l3_code": "L3-content", "l3_label": "不符"},
    "L3-supplier": {"l1_domain_code": "supplier", "l1_label": "供應商", "l2_code": "L2s", "l2_label": "履約", "l3_code": "L3-supplier", "l3_label": "遲到"},
}
_EMPTY_NODE = {k: "" for k in ("l1_domain_code", "l1_label", "l2_code", "l2_label", "l3_code", "l3_label")}


def _fake_sanitize(code: str, _cands) -> dict:
    return _L3_NODES.get(code, dict(_EMPTY_NODE))


@pytest.fixture
def finalize_env(monkeypatch, fixed_config):
    """固定 _finalize_attr 的 config 依賴（L3 解析 / 證據・abstain 政策），使斷言與 config 解耦。"""
    monkeypatch.setattr(prejudge, "_sanitize_l3", _fake_sanitize)
    monkeypatch.setattr(
        prejudge.global_rule, "evidence_policy", lambda: {"require_quote_grounded": True, "l3_min_confidence": 0.5}
    )
    monkeypatch.setattr(prejudge.global_rule, "abstain_policy", lambda: {"l3": "allow_empty_low_evidence"})


def test_evidence_grounded() -> None:
    """evidence_quote 須為原文逐字片段（去空白後 substring）且 ≥4 字，防編造證據。"""
    g = prejudge._evidence_grounded
    assert g("客服態度差且沒有回應訊息", "沒有回應") is True
    assert g("退 款 太 慢 了", "退款太慢") is True  # 去空白後比對
    assert g("完全不相關的內容", "編造的假證據") is False  # 非原文
    assert g("有效文字內容", "短") is False  # <4 字視為未佐證
    assert g("任何文字", "") is False  # 空 quote


def test_finalize_attr_grounded_high_conf_keeps_l3(finalize_env) -> None:
    """證據接地 + 高信心 + content 域（不封頂）→ 保留 L3、信心不變。"""
    attr = prejudge._finalize_attr(
        {"source": "pr", "source_id": "R1"},
        "商品頁描述與實際完全不符很誤導",
        {"l3_code": "L3-content", "confidence": 0.9, "evidence_quote": "描述與實際完全不符", "candidates": []},
        frozenset({"L3-content"}),
    )
    assert attr["l1_domain_code"] == "content"
    assert attr["l3_code"] == "L3-content"
    assert attr["confidence"] == pytest.approx(0.9)


def test_finalize_attr_ungrounded_drops_l3_and_presses_conf(finalize_env) -> None:
    """證據非原文（疑編造）→ L3 降階留空、保留 L1/L2、信心壓至 l3_min-0.01 交人審。"""
    attr = prejudge._finalize_attr(
        {"source": "pr", "source_id": "R1"},
        "完全不同的評論內容跟證據無關",
        {"l3_code": "L3-content", "confidence": 0.9, "evidence_quote": "這是編造的假證據", "candidates": []},
        frozenset({"L3-content"}),
    )
    assert attr["l1_domain_code"] == "content"  # L1/L2 保留
    assert attr["l3_code"] == ""  # L3 降階
    assert attr["confidence"] == pytest.approx(0.49)  # 壓至 l3_min(0.5)-0.01


def test_finalize_attr_abstain_backfills_from_candidate(finalize_env) -> None:
    """模型 abstain（l3_code 空）但有候選 → 取 top 候選回填 L1/L2/L3。"""
    attr = prejudge._finalize_attr(
        {"source": "pr", "source_id": "R1"},
        "商品描述與實際不符誤導消費",
        {"l3_code": "", "confidence": 0.7, "evidence_quote": "描述與實際不符", "candidates": [{"code": "L3-content", "score": 0.8}]},
        frozenset({"L3-content"}),
    )
    assert attr["l1_domain_code"] == "content"  # 從候選回填
    assert attr["l3_code"] == "L3-content"  # grounded + conf≥min → 保留
    assert attr["confidence"] == pytest.approx(0.7)


def test_finalize_attr_supplier_without_order_capped(finalize_env) -> None:
    """供應商域缺 order_oid → 證據封頂至 jury_high-0.01、標記 evidence_capped。"""
    attr = prejudge._finalize_attr(
        {"source": "pr", "source_id": "R1"},  # 無 order_oid
        "供應商臨時取消還遲到接送",
        {"l3_code": "L3-supplier", "confidence": 0.95, "evidence_quote": "臨時取消還遲到", "candidates": []},
        frozenset({"L3-supplier"}),
    )
    assert attr["l1_domain_code"] == "supplier"
    assert attr["confidence"] == pytest.approx(0.69)  # 封頂 jury_high(0.7)-0.01
    assert attr["evidence_capped"] is True

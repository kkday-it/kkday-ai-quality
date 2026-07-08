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
    """固定閾值 + prejudge 旋鈕（與 config 漂移解耦）。

    prejudge_depth 亦 pin 在 l3：本檔基準線鎖 cascade/flat 完整路徑行為；
    DB active 切 l2（初判只判 L1+L2）不應改變這批測試的走向。
    """
    monkeypatch.setattr(prejudge, "_tiers", lambda: dict(_FIXED_TIERS))
    monkeypatch.setattr(prejudge, "_prejudge_cfg", lambda: dict(_FIXED_CFG))
    monkeypatch.setattr(prejudge.global_rule, "prejudge_depth", lambda: "l3")


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
    """判決階段派生（歸因 finding 專用；混合中性歸因與負向同規則，polarity 僅 unknown 特判）。"""
    assert prejudge._derive_stage("unknown", "", "jury", False) == "insufficient"
    # 無 L3 或 evidence-cap → pending_data（不分負向/混合中性）
    assert prejudge._derive_stage("negative", "", "auto_accept", False) == "pending_data"
    assert prejudge._derive_stage("neutral", "", "jury", False) == "pending_data"
    assert prejudge._derive_stage("negative", "L3-1", "auto_accept", True) == "pending_data"
    # 有 L3 + 未 cap：高信心 judged、否則 pending_review
    assert prejudge._derive_stage("negative", "L3-1", "auto_accept", False) == "judged"
    assert prejudge._derive_stage("neutral", "L3-1", "auto_accept", False) == "judged"
    assert prejudge._derive_stage("negative", "L3-1", "jury", False) == "pending_review"
    assert prejudge._derive_stage("negative", "L3-1", "needs_review", False) == "pending_review"


def test_attribute_when_parses_config(monkeypatch) -> None:
    """極性閘門 config 解析：清單/legacy 字串皆收；只認 negative/neutral；缺失回退 {negative}。"""
    from app.core import global_rule

    monkeypatch.setattr(
        global_rule, "polarity_gate", lambda: {"attribute_when": ["negative", "neutral"]}
    )
    assert prejudge._attribute_when() == frozenset({"negative", "neutral"})
    # legacy 單值字串（attribute_only_when）
    monkeypatch.setattr(global_rule, "polarity_gate", lambda: {"attribute_only_when": "negative"})
    assert prejudge._attribute_when() == frozenset({"negative"})
    # 誤填 positive → 過濾；全無效回退保守舊行為
    monkeypatch.setattr(global_rule, "polarity_gate", lambda: {"attribute_when": ["positive"]})
    assert prejudge._attribute_when() == frozenset({"negative"})
    monkeypatch.setattr(global_rule, "polarity_gate", lambda: {})
    assert prejudge._attribute_when() == frozenset({"negative"})


def test_to_findings_neutral_enters_attribution(monkeypatch, fixed_config) -> None:
    """gate 含 neutral 時：混合中性評論進歸因；有問題點→歸因列帶 polarity=neutral，無→non_issue。"""
    from app.core import global_rule

    monkeypatch.setattr(
        global_rule, "polarity_gate", lambda: {"attribute_when": ["negative", "neutral"]}
    )
    monkeypatch.setattr(prejudge.client, "is_stub", lambda: False)
    monkeypatch.setattr(prejudge, "_stage1_polarity", lambda item, text, model: ("neutral", 3))
    monkeypatch.setattr(prejudge, "_skip0", lambda item, text: False)
    attr = {
        "l1_domain_code": "supplier",
        "l1_label": "供應商履約",
        "l2_code": "C-3-2",
        "l2_label": "成團履約",
        "l3_code": "C-3-2-1",
        "l3_label": "行程縮水",
        "confidence": 0.85,
        "raw_confidence": 0.85,
        "evidence_quote": "船沒搭到",
        "l3_candidates": [],
    }
    monkeypatch.setattr(prejudge, "_resolve_attrs_multi", lambda *a, **k: [dict(attr)])
    fs = prejudge.to_findings(
        {"source": "product_reviews", "source_id": "t1", "content": "整體很棒，只是船沒搭到"},
        model="m",
    )
    assert len(fs) == 1 and fs[0].polarity == "neutral" and fs[0].l1_domain_code == "supplier"
    assert fs[0].judgment_stage == "judged"  # 有 L3+高信心：與負向同規則派生
    # 混合中性但找不到具體問題點 → 純 non_issue（judged，非 pending_data）
    monkeypatch.setattr(prejudge, "_resolve_attrs_multi", lambda *a, **k: [])
    fs = prejudge.to_findings(
        {"source": "product_reviews", "source_id": "t2", "content": "整體很棒"}, model="m"
    )
    assert len(fs) == 1 and fs[0].l1_domain_code == "" and fs[0].judgment_stage == "judged"


def test_resolve_attrs_min_confidence_gate(monkeypatch, fixed_config) -> None:
    """attr 級最低信心閘門：低於 evidence_policy.attr_min_confidence 整條丟棄（湊數殭屍列）；0=關閉。"""
    from app.core import global_rule

    monkeypatch.setattr(prejudge.client, "is_stub", lambda: False)
    monkeypatch.setattr(global_rule, "cascade", lambda: {"enabled": False})
    low = {
        "l1_domain_code": "product_quality",
        "l2_code": "C-2-1",
        "l3_code": "",
        "confidence": 0.09,
    }
    ok = {"l1_domain_code": "supplier", "l2_code": "C-3-4", "l3_code": "", "confidence": 0.9}
    monkeypatch.setattr(prejudge, "_stage2_attribute_multi", lambda *a, **k: [dict(low), dict(ok)])
    monkeypatch.setattr(global_rule, "evidence_policy", lambda: {"attr_min_confidence": 0.2})
    out = prejudge._resolve_attrs_multi({}, "t", "m", 2)
    assert [a["l1_domain_code"] for a in out] == ["supplier"]  # 0.09 湊數列被殺
    monkeypatch.setattr(global_rule, "evidence_policy", lambda: {"attr_min_confidence": 0})
    out = prejudge._resolve_attrs_multi({}, "t", "m", 2)
    assert len(out) == 2  # 閘門關閉 → 保留（向後相容）


def test_resolve_attrs_low_conf_reroute(monkeypatch, fixed_config) -> None:
    """低信心負反饋重路由：Stage B 低於閘門的域排除後重跑 Stage A 改判他域；重判結果同過閘門。"""
    from app.core import global_rule

    monkeypatch.setattr(prejudge.client, "is_stub", lambda: False)
    monkeypatch.setattr(
        global_rule, "cascade", lambda: {"enabled": True, "reroute_on_low_conf": True}
    )
    monkeypatch.setattr(global_rule, "evidence_policy", lambda: {"attr_min_confidence": 0.2})
    calls: list[frozenset] = []

    def fake_stage_a(text, model, max_n, polarity="negative", exclude=frozenset()):
        calls.append(exclude)
        return ["customer"] if exclude else ["product_quality"]  # 首輪選錯域；重路由改判 customer

    def fake_stage_b(item, text, domain, model):
        conf = 0.09 if domain == "product_quality" else 0.8  # 錯域湊數低信心；正確域正常
        return {"l1_domain_code": domain, "l2_code": "", "l3_code": "", "confidence": conf}

    monkeypatch.setattr(prejudge, "_stage_a_domains_multi", fake_stage_a)
    monkeypatch.setattr(prejudge, "_stage_b", fake_stage_b)
    out = prejudge._resolve_attrs_multi({}, "t", "m", 2)
    assert [a["l1_domain_code"] for a in out] == ["customer"]  # 錯域被殺、重路由域成立
    assert len(calls) == 2 and "product_quality" in calls[1]  # 第二輪帶排除集
    # Stage B 棄權（空回）同樣觸發重路由（棄權=域選錯最常見訊號）
    calls.clear()
    monkeypatch.setattr(
        prejudge,
        "_stage_b",
        lambda item, text, d, model: {
            "l1_domain_code": "",
            "l2_code": "",
            "l3_code": "",
            "confidence": 0.5,
        }
        if d == "product_quality"
        else {"l1_domain_code": d, "l2_code": "", "l3_code": "", "confidence": 0.8},
    )
    out = prejudge._resolve_attrs_multi({}, "t", "m", 2)
    assert [a["l1_domain_code"] for a in out] == ["customer"] and len(calls) == 2
    # 重路由關閉 → 只跑一輪，低信心域被閘門殺掉、不重試
    calls.clear()
    monkeypatch.setattr(
        global_rule, "cascade", lambda: {"enabled": True, "reroute_on_low_conf": False}
    )
    out = prejudge._resolve_attrs_multi({}, "t", "m", 2)
    assert out == [] and len(calls) == 1


def test_resolve_attrs_stage_a_l1l2(monkeypatch, fixed_config) -> None:
    """stage_a_level=l1l2：Stage A 直選 L2 面向 → Stage B 以 (域, l2_code) 聚焦；重路由排除集=L2 顆粒度。"""
    from app.core import global_rule

    monkeypatch.setattr(prejudge.client, "is_stub", lambda: False)
    monkeypatch.setattr(
        global_rule,
        "cascade",
        lambda: {"enabled": True, "stage_a_level": "l1l2", "reroute_on_low_conf": True},
    )
    monkeypatch.setattr(global_rule, "evidence_policy", lambda: {"attr_min_confidence": 0.2})
    monkeypatch.setattr(
        prejudge, "_l2_domain_map", lambda: {"C-3-4": "supplier", "C-6-1": "customer"}
    )
    calls: list[frozenset] = []

    def fake_l2s(text, model, max_n, polarity="negative", exclude=frozenset()):
        calls.append(exclude)
        return ["C-6-1"] if exclude else ["C-3-4"]  # 首輪選錯面向；重路由改判 C-6-1

    seen_b: list[tuple[str, str]] = []

    def fake_stage_b(item, text, domain, model, l2_code=""):
        seen_b.append((domain, l2_code))
        conf = 0.1 if l2_code == "C-3-4" else 0.85
        return {
            "l1_domain_code": domain if conf >= 0.2 else "",
            "l2_code": l2_code,
            "l3_code": "",
            "confidence": conf,
        }

    monkeypatch.setattr(prejudge, "_stage_a_l2s_multi", fake_l2s)
    monkeypatch.setattr(prejudge, "_stage_b", fake_stage_b)
    out = prejudge._resolve_attrs_multi({}, "t", "m", 2)
    assert [a["l1_domain_code"] for a in out] == ["customer"]
    assert seen_b == [("supplier", "C-3-4"), ("customer", "C-6-1")]  # Stage B 收到面向聚焦
    assert len(calls) == 2 and "C-3-4" in calls[1]  # 重路由排除集為 L2 code


def test_to_findings_gate_excludes_neutral_when_config_negative_only(
    monkeypatch, fixed_config
) -> None:
    """gate 只列 negative 時：中性評論維持舊行為（non_issue 不歸因）。"""
    from app.core import global_rule

    monkeypatch.setattr(global_rule, "polarity_gate", lambda: {"attribute_when": ["negative"]})
    monkeypatch.setattr(prejudge.client, "is_stub", lambda: False)
    monkeypatch.setattr(prejudge, "_stage1_polarity", lambda item, text, model: ("neutral", 3))
    monkeypatch.setattr(prejudge, "_skip0", lambda item, text: False)
    called = []
    monkeypatch.setattr(prejudge, "_resolve_attrs_multi", lambda *a, **k: called.append(1) or [])
    fs = prejudge.to_findings(
        {"source": "product_reviews", "source_id": "t3", "content": "還行"}, model="m"
    )
    assert len(fs) == 1 and fs[0].l1_domain_code == "" and not called


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
    """stub 極性：回 (polarity, sentiment 1-5)。rating≤2 負(1-2) / ≥4 正(4-5) / 中間看負向詞 / 無詞有字 unknown / 無字 neutral(3)。"""
    pol = prejudge._stub_polarity
    assert pol({"rating": 1}, "隨便") == ("negative", 1)
    assert pol({"rating": 2}, "隨便") == ("negative", 2)
    assert pol({"rating": 5}, "隨便") == ("positive", 5)
    assert pol({"rating": 4}, "隨便") == ("positive", 4)
    assert pol({"rating": 3}, "要退款") == (
        "negative",
        1,
    )  # 中間 + 負向詞（無 rating 區間，預設 1）
    assert pol({"rating": 3}, "普通") == ("unknown", 0)  # 中間 + 無負向詞 + 有字
    assert pol({}, "誤導消費者") == ("negative", 1)  # 無 rating 靠負向詞
    assert pol({}, "") == ("neutral", 3)  # 無 rating 無字


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
    "L3-content": {
        "l1_domain_code": "content",
        "l1_label": "商品內容",
        "l2_code": "L2c",
        "l2_label": "描述",
        "l3_code": "L3-content",
        "l3_label": "不符",
    },
    "L3-supplier": {
        "l1_domain_code": "supplier",
        "l1_label": "供應商",
        "l2_code": "L2s",
        "l2_label": "履約",
        "l3_code": "L3-supplier",
        "l3_label": "遲到",
    },
}
_EMPTY_NODE = {
    k: "" for k in ("l1_domain_code", "l1_label", "l2_code", "l2_label", "l3_code", "l3_label")
}


def _fake_sanitize(code: str, _cands) -> dict:
    return _L3_NODES.get(code, dict(_EMPTY_NODE))


@pytest.fixture
def finalize_env(monkeypatch, fixed_config):
    """固定 _finalize_attr 的 config 依賴（L3 解析 / 證據・abstain 政策），使斷言與 config 解耦。"""
    monkeypatch.setattr(prejudge, "_sanitize_l3", _fake_sanitize)
    monkeypatch.setattr(
        prejudge.global_rule,
        "evidence_policy",
        lambda: {"require_quote_grounded": True, "l3_min_confidence": 0.5},
    )
    monkeypatch.setattr(
        prejudge.global_rule, "abstain_policy", lambda: {"l3": "allow_empty_low_evidence"}
    )


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
        {
            "l3_code": "L3-content",
            "confidence": 0.9,
            "evidence_quote": "描述與實際完全不符",
            "candidates": [],
        },
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
        {
            "l3_code": "L3-content",
            "confidence": 0.9,
            "evidence_quote": "這是編造的假證據",
            "candidates": [],
        },
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
        {
            "l3_code": "",
            "confidence": 0.7,
            "evidence_quote": "描述與實際不符",
            "candidates": [{"code": "L3-content", "score": 0.8}],
        },
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
        {
            "l3_code": "L3-supplier",
            "confidence": 0.95,
            "evidence_quote": "臨時取消還遲到",
            "candidates": [],
        },
        frozenset({"L3-supplier"}),
    )
    assert attr["l1_domain_code"] == "supplier"
    assert attr["confidence"] == pytest.approx(0.69)  # 封頂 jury_high(0.7)-0.01
    assert attr["evidence_capped"] is True


# ── 多歸因去重 / 排序 / 上限（_resolve_attrs_multi 的防過度歸因邏輯）──────────
# 鎖：同 L1 域保信心最高（action/owner 為域級）+ 濾全 abstain（無域）+ 依 confidence 降冪 + cap max_n。
# mock 掉 LLM 產出階段（_stage2_attribute_multi），只測其後的確定性去重/排序。


@pytest.fixture
def non_stub(monkeypatch):
    """離開 stub 模式（使 _resolve_attrs_multi 走真歸因分支）+ 關 cascade（走單次多歸因）+ pin l3 深度。"""
    monkeypatch.setattr(prejudge.client, "is_stub", lambda: False)
    monkeypatch.setattr(prejudge.global_rule, "cascade", lambda: {"enabled": False})
    monkeypatch.setattr(prejudge.global_rule, "prejudge_depth", lambda: "l3")


def test_resolve_attrs_multi_dedup_keeps_highest_and_drops_abstain(non_stub, monkeypatch) -> None:
    """同域保信心最高 + 濾空域 + 依信心降冪。"""
    synthetic = [
        {"l1_domain_code": "content", "confidence": 0.6},
        {"l1_domain_code": "content", "confidence": 0.9},  # 同域較高 → 保這條
        {"l1_domain_code": "supplier", "confidence": 0.7},
        {"l1_domain_code": "", "confidence": 0.95},  # 空域（全 abstain）→ 濾除
    ]
    monkeypatch.setattr(prejudge, "_stage2_attribute_multi", lambda *a, **k: synthetic)
    out = prejudge._resolve_attrs_multi({"source": "pr", "source_id": "R1"}, "text", "m", 3)
    assert [a["l1_domain_code"] for a in out] == ["content", "supplier"]  # 0.9 > 0.7 降冪
    assert out[0]["confidence"] == 0.9


def test_resolve_attrs_multi_caps_at_max_n(non_stub, monkeypatch) -> None:
    """歸因數封頂 max_n（依信心取前 N 域），防過度歸因。"""
    synthetic = [
        {"l1_domain_code": "content", "confidence": 0.9},
        {"l1_domain_code": "supplier", "confidence": 0.8},
        {"l1_domain_code": "service", "confidence": 0.7},
    ]
    monkeypatch.setattr(prejudge, "_stage2_attribute_multi", lambda *a, **k: synthetic)
    out = prejudge._resolve_attrs_multi({"source": "pr", "source_id": "R1"}, "text", "m", 2)
    assert [a["l1_domain_code"] for a in out] == ["content", "supplier"]  # 取前 2 高信心


def test_resolve_attrs_multi_stub_returns_empty(monkeypatch) -> None:
    """stub 模式無法真歸因 → 回空（負向但無違規線 → 上層轉 pending_data）。"""
    monkeypatch.setattr(prejudge.client, "is_stub", lambda: True)
    assert prejudge._resolve_attrs_multi({"source": "pr", "source_id": "R1"}, "text", "m", 2) == []


# ── G1 自動確認路由（_route_status / to_findings status）──────────────────────
def test_route_status_auto_confirm(monkeypatch) -> None:
    """auto_accept + judged → auto_confirmed（免人工佇列）；其餘 tier/stage → new。"""
    monkeypatch.setattr(
        prejudge, "_auto_confirm_cfg", lambda: {"enabled": True, "audit_sample_rate": 0.05}
    )
    monkeypatch.setattr(prejudge.random, "random", lambda: 0.5)  # > rate → 不抽樣
    assert prejudge._route_status("auto_accept", "judged") == "auto_confirmed"
    assert prejudge._route_status("jury", "judged") == "new"
    assert prejudge._route_status("auto_accept", "pending_review") == "new"
    assert prejudge._route_status("needs_review", "pending_data") == "new"


def test_route_status_sampled_back_to_new(monkeypatch) -> None:
    """auto_accept+judged 命中 audit_sample_rate 抽樣 → 回 new 交人工複核（防自動化偏誤）。"""
    monkeypatch.setattr(
        prejudge, "_auto_confirm_cfg", lambda: {"enabled": True, "audit_sample_rate": 0.05}
    )
    monkeypatch.setattr(prejudge.random, "random", lambda: 0.01)  # < rate → 抽樣
    assert prejudge._route_status("auto_accept", "judged") == "new"


def test_route_status_disabled_falls_back_to_new(monkeypatch) -> None:
    """停用自動確認 → 一律 new（回退舊行為）。"""
    monkeypatch.setattr(
        prejudge, "_auto_confirm_cfg", lambda: {"enabled": False, "audit_sample_rate": 0.05}
    )
    assert prejudge._route_status("auto_accept", "judged") == "new"


def test_to_findings_positive_routed_auto_confirmed(stub_engine, monkeypatch) -> None:
    """正向（auto_accept+judged）經 _route → status=auto_confirmed（不進人工佇列）。"""
    monkeypatch.setattr(
        prejudge, "_auto_confirm_cfg", lambda: {"enabled": True, "audit_sample_rate": 0.0}
    )
    out = prejudge.to_findings(_item(5, "整體體驗很好值得推薦給朋友"), model="gpt-5-nano")
    assert out[0].status == "auto_confirmed"


def test_to_findings_negative_pending_stays_new(stub_engine) -> None:
    """負向未歸因（needs_review+pending_data）→ status=new（需人工）。"""
    out = prejudge.to_findings(_item(1, "服務很差要退款"), model="gpt-5-nano")
    assert out[0].status == "new"

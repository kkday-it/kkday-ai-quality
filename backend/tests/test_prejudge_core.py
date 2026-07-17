"""初判核心（prejudge）確定性行為測試——Prompt-as-Source 唯一引擎路徑（legacy 已全數退役）。

分兩層，皆不需 LLM key（stub）/ 不碰 DB：
- **純 helper**：信心分層邊界、階段派生、證據封頂、寬鬆 float 解析、批次 effort 上限。
- **stub 管線**：`to_findings` 在 stub 模式的確定性啟發式（極性閘門 / 負向未歸因 pending_data）。

config 相關讀取（閾值 / 旋鈕）一律 monkeypatch 固定，使斷言與 config 漂移解耦。
"""

from __future__ import annotations

import pytest

from app.judge import prejudge

_FIXED_TIERS = {"auto_accept": 0.8, "jury_low": 0.5, "jury_high": 0.7}
_FIXED_CFG = {
    "max_attributions": 2,
}


@pytest.fixture(autouse=True)
def _decouple_taxonomy(monkeypatch):
    """evidence_gated / 域 action 固定（與 `## Taxonomy`/ai_judge/DB 解耦；派生正確性見
    test_prompt_source.test_structure_from_taxonomy）——避免單元測經 ai_judge 轉載全部 prompt。"""
    monkeypatch.setattr(prejudge, "_evidence_gated_domains", lambda: frozenset({"supplier"}))
    monkeypatch.setattr(prejudge, "_action_for", lambda dom: "escalate_ux")


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
    """初判階段派生（歸因 finding 專用；混合中性歸因與負向同規則）。"""
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
    monkeypatch.setattr(
        prejudge, "_polarity_gate_cfg", lambda: {"attribute_when": ["negative", "neutral"]}
    )
    assert prejudge._attribute_when() == frozenset({"negative", "neutral"})
    # legacy 單值字串（attribute_only_when）
    monkeypatch.setattr(prejudge, "_polarity_gate_cfg", lambda: {"attribute_only_when": "negative"})
    assert prejudge._attribute_when() == frozenset({"negative"})
    # 誤填 positive → 過濾；全無效回退保守舊行為
    monkeypatch.setattr(prejudge, "_polarity_gate_cfg", lambda: {"attribute_when": ["positive"]})
    assert prejudge._attribute_when() == frozenset({"negative"})
    monkeypatch.setattr(prejudge, "_polarity_gate_cfg", lambda: {})
    assert prejudge._attribute_when() == frozenset({"negative"})


def test_to_findings_neutral_enters_attribution(monkeypatch, fixed_config) -> None:
    """gate 含 neutral 時：混合中性評論進歸因；有問題點→歸因列帶 polarity=neutral，無→non_issue。"""
    monkeypatch.setattr(
        prejudge, "_polarity_gate_cfg", lambda: {"attribute_when": ["negative", "neutral"]}
    )
    monkeypatch.setattr(prejudge.client, "is_stub", lambda: False)
    monkeypatch.setattr(prejudge, "_stage1_polarity", lambda item, text, model, **k: ("neutral", 3))
    attr = {
        "l1_domain_code": "supplier",
        "l1_label": "供應商履約",
        "l2_code": "C-3-2",
        "l2_label": "成團履約",
        "l3_code": "",
        "l3_label": "",
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
    assert fs[0].prejudge_stage == "judged"  # 有 L2+高信心：與負向同規則派生
    # 混合中性但找不到具體問題點 → 純 non_issue（judged，非 pending_data）
    monkeypatch.setattr(prejudge, "_resolve_attrs_multi", lambda *a, **k: [])
    fs = prejudge.to_findings(
        {"source": "product_reviews", "source_id": "t2", "content": "整體很棒"}, model="m"
    )
    assert len(fs) == 1 and fs[0].l1_domain_code == "" and fs[0].prejudge_stage == "judged"


def test_to_findings_gate_excludes_neutral_when_config_negative_only(
    monkeypatch, fixed_config
) -> None:
    """gate 只列 negative 時：中性評論維持舊行為（non_issue 不歸因）。"""
    monkeypatch.setattr(prejudge, "_polarity_gate_cfg", lambda: {"attribute_when": ["negative"]})
    monkeypatch.setattr(prejudge.client, "is_stub", lambda: False)
    monkeypatch.setattr(prejudge, "_stage1_polarity", lambda item, text, model, **k: ("neutral", 3))
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


def test_stub_polarity_heuristic(fixed_config) -> None:
    """stub 極性（純 rating 分流；keyword 機制 2026-07-16 全棧退役）：
    rating≤2 負(1-2) / ≥4 正(4-5) / 3 或無 rating → 中立 3。"""
    pol = prejudge._stub_polarity
    assert pol({"rating": 1}) == ("negative", 1)
    assert pol({"rating": 2}) == ("negative", 2)
    assert pol({"rating": 5}) == ("positive", 5)
    assert pol({"rating": 4}) == ("positive", 4)
    assert pol({"rating": 3}) == ("neutral", 3)  # 中間 → 中立 3
    assert pol({}) == ("neutral", 3)  # 無 rating → 中立 3


def test_cap_batch_reasoning_effort(monkeypatch) -> None:
    """批次 effort 硬上限：超限壓檔、未超限/未知值原樣放行、cap 缺失不動作。"""
    monkeypatch.setattr(prejudge, "_prejudge_cfg", lambda: {"batch_max_reasoning_effort": "medium"})
    assert prejudge.cap_batch_reasoning_effort("xhigh") == "medium"  # 超限壓檔
    assert prejudge.cap_batch_reasoning_effort("high") == "medium"
    assert prejudge.cap_batch_reasoning_effort("low") == "low"  # 未超限原樣
    assert prejudge.cap_batch_reasoning_effort("medium") == "medium"
    assert prejudge.cap_batch_reasoning_effort(None) is None  # 空值放行
    assert prejudge.cap_batch_reasoning_effort("default") == "default"  # 未知值放行
    monkeypatch.setattr(prejudge, "_prejudge_cfg", lambda: {})  # cap 缺失 → 不動作
    assert prejudge.cap_batch_reasoning_effort("xhigh") == "xhigh"


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
    """純好評（rating=5 短評）→ 單一非問題正向 finding（stub rating 啟發式，不歸因）。"""
    out = prejudge.to_findings(_item(5, "讚"), model="gpt-5-nano")
    assert len(out) == 1
    f = out[0]
    assert f.polarity == "positive"
    assert f.prejudge_stage == "judged"
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
    assert f.prejudge_stage == "pending_data"
    assert f.confidence_tier == "needs_review"
    assert f.needs_review is True


def test_to_findings_middling_review_neutral_judged(stub_engine) -> None:
    """中間評分（rating=3）→ 中立 3 → judged（傾向只有三態，無 unknown/insufficient）。"""
    out = prejudge.to_findings(_item(3, "普通"), model="gpt-5-nano")
    assert len(out) == 1
    f = out[0]
    assert f.polarity == "neutral"
    assert f.sentiment_score == 3
    assert f.prejudge_stage == "judged"


# ── 歸因處理正確性（_evidence_grounded / _finalize_attr_l2）─────────────────
# 這批鎖「LLM 原始輸出 → 淨化 attr」的確定性邏輯——證據接地防編造、白名單校驗、證據封頂——
# 是初判核心（_attrs_pack 六域並行的合流前處理）的正確性關鍵。

# 面向白名單（_sanitize_l2 用；code → (l1_domain, l1_label, l2_label)）。
_L2_VALID = {
    "C-1-3": ("content", "商品內容", "描述"),
    "C-3-2": ("supplier", "供應商履約", "履約"),
}


@pytest.fixture
def finalize_l2_env(monkeypatch, fixed_config):
    """固定 _finalize_attr_l2 的 config 依賴（證據政策），使斷言與 config 解耦。"""
    monkeypatch.setattr(
        prejudge,
        "_evidence_policy",
        lambda: {"require_quote_grounded": True},
    )


def test_evidence_grounded() -> None:
    """evidence_quote 須為原文逐字片段（去空白後 substring）且 ≥4 字，防編造證據。"""
    g = prejudge._evidence_grounded
    assert g("客服態度差且沒有回應訊息", "沒有回應") is True
    assert g("退 款 太 慢 了", "退款太慢") is True  # 去空白後比對
    assert g("完全不相關的內容", "編造的假證據") is False  # 非原文
    assert g("有效文字內容", "短") is False  # <4 字視為未佐證
    assert g("任何文字", "") is False  # 空 quote


def test_finalize_attr_l2_grounded_keeps_confidence(finalize_l2_env) -> None:
    """證據接地 + content 域（不封頂）→ 保留 l2_code、信心不變。"""
    attr = prejudge._finalize_attr_l2(
        {"source": "pr", "source_id": "R1"},
        "商品頁描述與實際完全不符很誤導",
        {"l2_code": "C-1-3", "confidence": 0.9, "evidence_quote": "描述與實際完全不符"},
        _L2_VALID,
    )
    assert attr["l1_domain_code"] == "content"
    assert attr["l2_code"] == "C-1-3"
    assert attr["confidence"] == pytest.approx(0.9)


def test_finalize_attr_l2_ungrounded_presses_confidence(finalize_l2_env) -> None:
    """證據非原文（疑編造）→ 信心壓至 jury_low-0.01 交人審；l2_code 仍保留（非 L3 abstain 機制）。"""
    attr = prejudge._finalize_attr_l2(
        {"source": "pr", "source_id": "R1"},
        "完全不同的評論內容跟證據無關",
        {"l2_code": "C-1-3", "confidence": 0.9, "evidence_quote": "這是編造的假證據"},
        _L2_VALID,
    )
    assert attr["l2_code"] == "C-1-3"
    assert attr["confidence"] == pytest.approx(0.49)  # 壓至 jury_low(0.5)-0.01


def test_finalize_attr_l2_unknown_code_returns_empty(finalize_l2_env) -> None:
    """l2_code 不在白名單（幻覺 code）→ 全空（未歸類，視同棄權）。"""
    attr = prejudge._finalize_attr_l2(
        {"source": "pr", "source_id": "R1"},
        "某評論文字",
        {"l2_code": "C-9-9", "confidence": 0.9, "evidence_quote": "某評論文字"},
        _L2_VALID,
    )
    assert attr["l1_domain_code"] == "" and attr["l2_code"] == ""


def test_finalize_attr_l2_supplier_without_order_capped(finalize_l2_env) -> None:
    """供應商域缺 order_oid → 證據封頂至 jury_high-0.01、標記 evidence_capped。"""
    attr = prejudge._finalize_attr_l2(
        {"source": "pr", "source_id": "R1"},  # 無 order_oid
        "供應商臨時取消還遲到接送",
        {"l2_code": "C-3-2", "confidence": 0.95, "evidence_quote": "臨時取消還遲到"},
        _L2_VALID,
    )
    assert attr["l1_domain_code"] == "supplier"
    assert attr["confidence"] == pytest.approx(0.69)  # 封頂 jury_high(0.7)-0.01
    assert attr["evidence_capped"] is True


# ── 多歸因去重 / 排序 / 上限（_resolve_attrs_multi 的防過度歸因邏輯）──────────
# 鎖：同(域,面向)去重保信心最高（同 L1 不同 L2 面向並列）+ 濾全 abstain（無域）+ 依 confidence 降冪 + cap max_n。
# mock 掉 LLM 產出階段（_attrs_pack），只測其後的確定性去重/排序（六域並行的合流尾段）。


@pytest.fixture
def non_stub(monkeypatch):
    """離開 stub 模式（使 _resolve_attrs_multi 走真歸因分支）。"""
    monkeypatch.setattr(prejudge.client, "is_stub", lambda: False)


def test_resolve_attrs_multi_dedup_keeps_highest_and_drops_abstain(non_stub, monkeypatch) -> None:
    """同域保信心最高 + 濾空域 + 依信心降冪。"""
    synthetic = [
        {"l1_domain_code": "content", "confidence": 0.6},
        {"l1_domain_code": "content", "confidence": 0.9},  # 同域較高 → 保這條
        {"l1_domain_code": "supplier", "confidence": 0.7},
        {"l1_domain_code": "", "confidence": 0.95},  # 空域（全 abstain）→ 濾除
    ]
    monkeypatch.setattr(prejudge, "_attrs_pack", lambda *a, **k: synthetic)
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
    monkeypatch.setattr(prejudge, "_attrs_pack", lambda *a, **k: synthetic)
    out = prejudge._resolve_attrs_multi({"source": "pr", "source_id": "R1"}, "text", "m", 2)
    assert [a["l1_domain_code"] for a in out] == ["content", "supplier"]  # 取前 2 高信心


def test_resolve_attrs_multi_same_domain_multi_l2_coexist(non_stub, monkeypatch) -> None:
    """同一 L1 域下多個 L2 面向並列（不再塌成一條）；僅同(域,面向)重複才去重取信心最高。"""
    monkeypatch.setattr(
        prejudge, "_evidence_policy", lambda: {}
    )  # 關閉 secondary/attr 閘門，純測去重粒度
    synthetic = [
        {"l1_domain_code": "service", "l2_code": "C-5-1", "l3_code": "", "confidence": 0.9},
        {
            "l1_domain_code": "service",
            "l2_code": "C-5-2",
            "l3_code": "",
            "confidence": 0.6,
        },  # 同域異面向 → 並列
        {
            "l1_domain_code": "service",
            "l2_code": "C-5-1",
            "l3_code": "",
            "confidence": 0.4,
        },  # 同(域,面向) → 被 0.9 覆蓋
    ]
    monkeypatch.setattr(prejudge, "_attrs_pack", lambda *a, **k: synthetic)
    out = prejudge._resolve_attrs_multi({"source": "pr", "source_id": "R1"}, "text", "m", 6)
    assert [(a["l1_domain_code"], a["l2_code"]) for a in out] == [
        ("service", "C-5-1"),  # 0.9 主
        ("service", "C-5-2"),  # 0.6 同域第二面向並列
    ]


def test_resolve_attrs_multi_stub_returns_empty(monkeypatch) -> None:
    """stub 模式無法真歸因 → 回空（負向但無違規線 → 上層轉 pending_data）。"""
    monkeypatch.setattr(prejudge.client, "is_stub", lambda: True)
    assert prejudge._resolve_attrs_multi({"source": "pr", "source_id": "R1"}, "text", "m", 2) == []


def test_resolve_attrs_min_confidence_gate(non_stub, monkeypatch) -> None:
    """attr 級最低信心閘門：低於 evidence_policy.attr_min_confidence 整條丟棄（湊數殭屍列）；0=關閉。"""
    low = {"l1_domain_code": "quality", "l2_code": "C-2-1", "l3_code": "", "confidence": 0.09}
    ok = {"l1_domain_code": "supplier", "l2_code": "C-3-4", "l3_code": "", "confidence": 0.9}
    monkeypatch.setattr(prejudge, "_attrs_pack", lambda *a, **k: [dict(low), dict(ok)])
    monkeypatch.setattr(prejudge, "_evidence_policy", lambda: {"attr_min_confidence": 0.2})
    out = prejudge._resolve_attrs_multi({}, "t", "m", 2)
    assert [a["l1_domain_code"] for a in out] == ["supplier"]  # 0.09 湊數列被殺
    monkeypatch.setattr(prejudge, "_evidence_policy", lambda: {"attr_min_confidence": 0})
    out = prejudge._resolve_attrs_multi({}, "t", "m", 2)
    assert len(out) == 2  # 閘門關閉 → 保留（向後相容）


def test_resolve_attrs_secondary_min_confidence_gate(non_stub, monkeypatch) -> None:
    """次要歸因信心閘門：非 primary 條目低於 secondary_min_confidence 丟棄只留主因；primary 不受影響；缺鍵=關閉。"""
    primary = {"l1_domain_code": "supplier", "l2_code": "C-3-4", "l3_code": "", "confidence": 0.9}
    weak2nd = {"l1_domain_code": "customer", "l2_code": "C-6-3", "l3_code": "", "confidence": 0.55}
    monkeypatch.setattr(prejudge, "_attrs_pack", lambda *a, **k: [dict(primary), dict(weak2nd)])
    monkeypatch.setattr(
        prejudge,
        "_evidence_policy",
        lambda: {"attr_min_confidence": 0.2, "secondary_min_confidence": 0.6},
    )
    out = prejudge._resolve_attrs_multi({}, "t", "m", 2)
    assert [a["l1_domain_code"] for a in out] == ["supplier"]  # 0.55 次要歸因被殺、主因保留
    # 單條 primary 信心 0.4（低於 secondary 閘門、高於 attr 閘門）→ 不受影響（閘門只管非 primary）
    lone = {"l1_domain_code": "customer", "l2_code": "C-6-3", "l3_code": "", "confidence": 0.4}
    monkeypatch.setattr(prejudge, "_attrs_pack", lambda *a, **k: [dict(lone)])
    out = prejudge._resolve_attrs_multi({}, "t", "m", 2)
    assert len(out) == 1
    # 閘門關閉（缺鍵=0）→ 兩條都保留（向後相容）
    monkeypatch.setattr(prejudge, "_evidence_policy", lambda: {"attr_min_confidence": 0.2})
    monkeypatch.setattr(prejudge, "_attrs_pack", lambda *a, **k: [dict(primary), dict(weak2nd)])
    out = prejudge._resolve_attrs_multi({}, "t", "m", 2)
    assert len(out) == 2


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
    # 系統判決留痕：判決人＝system:auto_confirm、初判時間非空（可追溯）
    assert out[0].verdict_by == "system:auto_confirm" and out[0].verdict_at


def test_to_findings_negative_pending_stays_new(stub_engine) -> None:
    """負向未歸因（needs_review+pending_data）→ status=new（需人工）。"""
    out = prejudge.to_findings(_item(1, "服務很差要退款"), model="gpt-5-nano")
    assert out[0].status == "new"

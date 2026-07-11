"""L2 深度初判（global_rule.prejudge_depth="l2"）：單呼叫多歸因 + 低信心負反饋重問 + stage 派生。"""

from __future__ import annotations

from app.core import global_rule
from app.judge import prejudge

_VALID = {
    "C-1-2": ("content", "商品內容", "行程資訊"),
    "C-4-1": ("supplier", "供應商履約", "現場執行"),
}


def _patch_l2_env(monkeypatch, *, reroute: bool = False, amin: float = 0.2):
    """L2 深度測試共用環境：depth=l2 + 面向白名單 + 政策 + 非 stub。"""
    monkeypatch.setattr(global_rule, "prejudge_depth", lambda: "l2")
    monkeypatch.setattr(prejudge, "_l2_label_map", lambda: dict(_VALID))
    monkeypatch.setattr(global_rule, "cascade", lambda: {"reroute_on_low_conf": reroute})
    monkeypatch.setattr(
        global_rule,
        "evidence_policy",
        lambda: {"require_quote_grounded": True, "attr_min_confidence": amin},
    )
    monkeypatch.setattr(prejudge.client, "is_stub", lambda: False)
    monkeypatch.setattr(
        prejudge, "_tiers", lambda: {"auto_accept": 0.8, "jury_low": 0.5, "jury_high": 0.7}
    )


def test_prejudge_depth_accessor(monkeypatch):
    """prejudge_depth：缺鍵/非法值回退 l3；合法 l2 生效。"""
    monkeypatch.setattr(global_rule, "_load", lambda: {"prejudge_depth": "l2"})
    assert global_rule.prejudge_depth() == "l2"
    monkeypatch.setattr(global_rule, "_load", lambda: {})
    assert global_rule.prejudge_depth() == "l3"
    monkeypatch.setattr(global_rule, "_load", lambda: {"prejudge_depth": "L9"})
    assert global_rule.prejudge_depth() == "l3"


def test_l2_depth_single_call_produces_l2_only_attrs(monkeypatch):
    """L2 深度：單呼叫產出 L1+L2 歸因、L3 恆空；非法 code 整條丟棄（白名單）。"""
    _patch_l2_env(monkeypatch)
    calls: list = []
    monkeypatch.setattr(
        prejudge,
        "_call",
        lambda *a, **k: (
            calls.append(1)
            or {
                "attributions": [
                    {
                        "l2_code": "C-1-2",
                        "confidence": 0.9,
                        "summary": [{"lang": "zh-tw", "text": "行程與頁面不符"}],
                        "evidence_quote": "雨天沒座位",
                    },
                    {
                        "l2_code": "C-9-9",
                        "confidence": 0.9,
                        "summary": [],
                        "evidence_quote": "雨天沒座位",
                    },
                ]
            }
        ),
    )
    attrs = prejudge._resolve_attrs_multi({}, "因為下雨天沒座位很失望", "m", 2, "negative")
    assert len(calls) == 1  # 單呼叫（無 Stage B）
    assert len(attrs) == 1  # 幻覺 code C-9-9 被白名單丟棄
    a = attrs[0]
    assert a["l1_domain_code"] == "content" and a["l2_code"] == "C-1-2"
    assert a["l3_code"] == ""  # L3 留待深判
    assert a["summary"] == {"zh-tw": "行程與頁面不符"}


def test_l2_depth_stage_derivation_not_pending_data(monkeypatch):
    """L2 深度下 L3 空是常態：高信心 L2 歸因 stage=judged（非 pending_data），G1 路由照走。"""
    _patch_l2_env(monkeypatch)
    attr = {
        "l1_domain_code": "content",
        "l1_label": "商品內容",
        "l2_code": "C-1-2",
        "l2_label": "行程資訊",
        "l3_code": "",
        "l3_label": "",
        "confidence": 0.9,
        "raw_confidence": 0.9,
        "summary": {"zh-tw": "x"},
        "evidence_quote": "q",
        "l3_candidates": [],
        "evidence_capped": False,
    }
    f = prejudge._attributed_finding(
        {"source": "product_reviews", "source_id": "R1"}, attr, "m", enhanced=False
    )
    assert f.judgment_stage == "judged"
    assert f.confidence_tier == "auto_accept"
    # 對照：l3 深度下同樣 L3 空 → pending_data（語義不變）
    monkeypatch.setattr(global_rule, "prejudge_depth", lambda: "l3")
    f3 = prejudge._attributed_finding(
        {"source": "product_reviews", "source_id": "R1"}, attr, "m", enhanced=False
    )
    assert f3.judgment_stage == "pending_data"


def test_l2_depth_ungrounded_evidence_demoted_to_review(monkeypatch):
    """低信心反饋第一環：evidence_quote 非原文逐字片段（疑編造）→ 信心壓入人審帶。"""
    _patch_l2_env(monkeypatch)
    monkeypatch.setattr(
        prejudge,
        "_call",
        lambda *a, **k: {
            "attributions": [
                {
                    "l2_code": "C-1-2",
                    "confidence": 0.95,
                    "summary": [],
                    "evidence_quote": "完全不存在的句子",
                }
            ]
        },
    )
    attrs = prejudge._resolve_attrs_multi({}, "因為下雨天沒座位很失望", "m", 2, "negative")
    assert attrs and attrs[0]["confidence"] < 0.5  # 壓到 jury_low 以下（needs_review 帶交人審）


def test_l2_depth_low_conf_reroute_retries_with_exclusion(monkeypatch):
    """低信心反饋第三環（負反饋重問）：首輪面向低於 attr 閘門 → 排除後重問一次，改判他面向。"""
    _patch_l2_env(monkeypatch, reroute=True, amin=0.2)
    monkeypatch.setattr(prejudge.ai_judge, "path_label", lambda c: c)
    seen_schemas: list = []
    seq = iter(
        [
            {
                "attributions": [
                    {
                        "l2_code": "C-1-2",
                        "confidence": 0.1,
                        "summary": [],
                        "evidence_quote": "雨天沒座位",
                    }
                ]
            },
            {
                "attributions": [
                    {
                        "l2_code": "C-4-1",
                        "confidence": 0.8,
                        "summary": [],
                        "evidence_quote": "雨天沒座位",
                    }
                ]
            },
        ]
    )

    def _fake_call(system, user, stage, model, *, schema=None, effort=None):
        seen_schemas.append(schema)
        return next(seq)

    monkeypatch.setattr(prejudge, "_call", _fake_call)
    attrs = prejudge._resolve_attrs_multi({}, "因為下雨天沒座位很失望", "m", 2, "negative")
    assert len(seen_schemas) == 2  # 首輪 + 重問一次
    # 重問 schema enum 已硬排除首輪低信心面向
    retry_enum = seen_schemas[1]["properties"]["attributions"]["items"]["properties"]["l2_code"][
        "enum"
    ]
    assert "C-1-2" not in retry_enum and "C-4-1" in retry_enum
    # 首輪低信心列被共用 attr 閘門丟棄，重問結果成立
    assert [a["l2_code"] for a in attrs] == ["C-4-1"]

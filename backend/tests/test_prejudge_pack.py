"""Prompt-as-Source 引擎確定性行為測試（判決引擎唯一路徑，legacy 已全數退役）。

覆蓋：
- _stage1_polarity → _pack_polarity 吃 00_polarity（parse 出 polarity/sentiment）；
- _resolve_attrs_multi → _attrs_pack 六域並行 → 各域 attribution 合流 → 共用尾段（同域去重/排序/閘門）。

不需 LLM key / DB：_call、prompt_source.load、_l2_label_map 全 monkeypatch 固定。
"""

from __future__ import annotations

from app.core import global_rule
from app.judge import prejudge, prompt_source

# 測試用面向白名單（_sanitize_l2 回填 l1/l2 label 用）
_VALID = {
    "C-3-1": ("supplier", "供應商履約", "人員服務"),
    "C-1-2": ("content", "商品內容", "行程資訊"),
}


def _pack_env(monkeypatch, *, amin: float = 0.2):
    """引擎共用環境：非 stub + 面向白名單 + 政策。"""
    monkeypatch.setattr(prejudge.client, "is_stub", lambda: False)
    monkeypatch.setattr(prejudge, "_l2_label_map", lambda: dict(_VALID))
    monkeypatch.setattr(
        prejudge, "_tiers", lambda: {"auto_accept": 0.8, "jury_low": 0.5, "jury_high": 0.7}
    )
    monkeypatch.setattr(
        global_rule,
        "evidence_policy",
        lambda: {"require_quote_grounded": True, "attr_min_confidence": amin},
    )
    monkeypatch.setattr(prejudge, "_attr_effort", lambda: None)
    monkeypatch.setattr(prejudge, "_polarity_effort", lambda: None)
    monkeypatch.setattr(prejudge, "_stage1_model", lambda m: m)

    def _fake_load(pid: str) -> dict:
        # user_template 帶兩槽；system 內嵌 pid 供 _fake_call 分辨是哪支域 prompt。
        return {
            "title": pid,
            "system": f"SYS::{pid}",
            "user_template": "傾向：{POLARITY}\n{TEXT}",
            "schema": {"type": "object"},
        }

    monkeypatch.setattr(prompt_source, "load", _fake_load)


def test_render_pack_user_fills_slots():
    """{TEXT}/{POLARITY} 槽位以 replace 填入（不受裸大括號影響）。"""
    out = prejudge._render_pack_user("傾向：{POLARITY}｜{TEXT}", "行程很棒", "negative")
    assert out == "傾向：negative｜行程很棒"


def test_pack_polarity_parses_prompt_output(monkeypatch):
    """_pack_polarity 吃 00_polarity 輸出並 clamp sentiment 與 polarity 一致。"""
    _pack_env(monkeypatch)
    monkeypatch.setattr(prejudge, "_call", lambda *a, **k: {"polarity": "negative", "sentiment": 5})
    pol, sent = prejudge._pack_polarity({}, "很失望", "gpt-5-mini")
    assert pol == "negative"
    assert sent == 2  # clamp 進 negative 區間（1-2），即使 LLM 回 5


def test_attrs_pack_merges_six_domains_and_dedups(monkeypatch):
    """六域並行：只有 supplier 域回歸因 → 合流後恰一條 C-3-1（其餘域空）。"""
    _pack_env(monkeypatch)
    text = "現場導遊態度很差全程臭臉"

    def _fake_call(system, user, stage, model, *, schema=None, effort=None):
        # 僅供應商域 prompt 回一條；其餘五域回空陣列
        if system == "SYS::03_C-3_supplier":
            return {
                "attributions": [
                    {
                        "l2_code": "C-3-1",
                        "confidence": 0.9,
                        "summary": [{"lang": "zh-tw", "text": "人員服務態度差"}],
                        "evidence_quote": "態度很差",
                    }
                ]
            }
        return {"attributions": []}

    monkeypatch.setattr(prejudge, "_call", _fake_call)
    # 帶 order_oid：避開 supplier 域「缺訂單佐證→證據封頂」，使斷言聚焦合流本身
    attrs = prejudge._resolve_attrs_multi({"order_oid": "O1"}, text, "gpt-5-mini", 2, "negative")
    assert len(attrs) == 1
    assert attrs[0]["l2_code"] == "C-3-1"
    assert attrs[0]["l1_domain_code"] == "supplier"  # l2→l1 由 _l2_label_map 映射（＝該 prompt 域）
    assert attrs[0]["confidence"] == 0.9


def test_attrs_pack_ranks_and_caps(monkeypatch):
    """兩域各回一條 → 依 confidence 降冪排序、截 max_n；低於 attr_min 的整條丟棄。"""
    _pack_env(monkeypatch, amin=0.2)
    text = "導遊態度差而且行程表寫得不清楚"

    def _fake_call(system, user, stage, model, *, schema=None, effort=None):
        if system == "SYS::03_C-3_supplier":
            return {
                "attributions": [
                    {
                        "l2_code": "C-3-1",
                        "confidence": 0.6,
                        "summary": [{"lang": "zh-tw", "text": "態度"}],
                        "evidence_quote": "態度差",
                    }
                ]
            }
        if system == "SYS::01_C-1_content":
            return {
                "attributions": [
                    {
                        "l2_code": "C-1-2",
                        "confidence": 0.85,
                        "summary": [{"lang": "zh-tw", "text": "行程描述"}],
                        "evidence_quote": "行程表寫得不清楚",
                    }
                ]
            }
        return {"attributions": []}

    monkeypatch.setattr(prejudge, "_call", _fake_call)
    attrs = prejudge._resolve_attrs_multi({}, text, "gpt-5-mini", 2, "negative")
    assert [a["l2_code"] for a in attrs] == ["C-1-2", "C-3-1"]  # 0.85 在前、0.6 在後


def test_attrs_pack_stub_returns_empty(monkeypatch):
    """stub 模式 _attrs_pack 回空（負向無違規線 → to_findings 產 pending_data）。"""
    _pack_env(monkeypatch)
    monkeypatch.setattr(prejudge.client, "is_stub", lambda: True)
    assert prejudge._attrs_pack({}, "文字", "m", 2, "negative") == []

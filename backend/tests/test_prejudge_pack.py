"""Prompt-as-Source 引擎確定性行為測試（判決引擎唯一路徑，legacy 已全數退役）。

覆蓋：
- _stage1_polarity → _pack_polarity 吃 00_polarity（parse 出 polarity/sentiment）；
- _resolve_attrs_multi → _attrs_pack 六域並行 → 各域 attribution 合流 → 共用尾段（同(域,面向)去重/排序/閘門）。

不需 LLM key / DB：_call、prompt_source.load、_l2_label_map 全 monkeypatch 固定。
"""

from __future__ import annotations

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
        prejudge,
        "_evidence_policy",
        lambda: {"require_quote_grounded": True, "attr_min_confidence": amin},
    )
    monkeypatch.setattr(prejudge, "_attr_effort", lambda: None)
    monkeypatch.setattr(prejudge, "_polarity_effort", lambda: None)
    monkeypatch.setattr(prejudge, "_stage1_model", lambda m: m)

    def _fake_load(pid: str, versions=None) -> dict:
        # user_template 帶兩槽；system 內嵌 pid 供 _fake_call 分辨是哪支域 prompt。
        # taxonomy 為 dict（供 structure()/evidence_gated 派生；supplier 標 evidence_gated）。
        dom = prompt_source._domain_of(pid) or "content"
        return {
            "title": pid,
            "system": f"SYS::{pid}",
            "user_template": "傾向：{POLARITY}\n{TEXT}",
            # 對齊現行 md schema（不含 abstain_reason——production 已移除，沙盒 overlay 才動態加）
            "schema": {
                "type": "object",
                "properties": {"attributions": {}},
                "required": ["attributions"],
            },
            "taxonomy": {
                "code": dom,
                "label": dom,
                "action": "x",
                "evidence_gated": dom == "supplier",
                "children": [{"code": "C-9-1", "label": "f"}],
            },
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

    def _fake_call(
        system, user, stage, model, *, schema=None, effort=None, label=None, cache_key=None
    ):
        # 僅供應商域 prompt 回一條；其餘五域回空陣列
        if "SYS::03_C-3_supplier" in system:
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

    def _fake_call(
        system, user, stage, model, *, schema=None, effort=None, label=None, cache_key=None
    ):
        if "SYS::03_C-3_supplier" in system:
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
        if "SYS::01_C-1_content" in system:
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


def test_resolve_attrs_all_abstain_no_reason_collection(monkeypatch):
    """六域全棄權 → 回 ([], "")——production 不收集棄權理由（abstain_reason 已自 md ## Schema
    移除，2026-07-16 以最新 prompt 為準；棄權理由僅 Prompt 測試沙盒經診斷 overlay 提供）。"""
    _pack_env(monkeypatch)

    def _fake_call(
        system, user, stage, model, *, schema=None, effort=None, label=None, cache_key=None
    ):
        # production schema 不含 abstain_reason（沙盒 overlay 才動態加）——回歸鎖
        assert "abstain_reason" not in (schema.get("required") or [])
        return {"attributions": []}

    monkeypatch.setattr(prejudge, "_call", _fake_call)
    attrs = prejudge._resolve_attrs_multi({}, "整體不太行", "gpt-5-mini", 2, "negative")
    assert attrs == []


def test_gated_out_candidates_yield_empty(monkeypatch):
    """域有回歸因但低於 attr_min 被閘掉 → 回空清單。"""
    _pack_env(monkeypatch, amin=0.5)

    def _fake_call(
        system, user, stage, model, *, schema=None, effort=None, label=None, cache_key=None
    ):
        if "SYS::01_C-1_content" in system:
            return {
                "attributions": [
                    {
                        "l2_code": "C-1-2",
                        "confidence": 0.3,
                        "summary": [{"lang": "zh-tw", "text": "行程描述"}],
                        "evidence_quote": "行程表寫得不清楚",
                    }
                ]
            }
        return {"attributions": []}

    monkeypatch.setattr(prejudge, "_call", _fake_call)
    attrs = prejudge._resolve_attrs_multi({}, "行程表寫得不清楚", "gpt-5-mini", 2, "negative")
    assert attrs == []  # 低於 attr_min 的候選被閘門刷掉，不成違規線


def test_old_version_schema_without_field_degrades_gracefully(monkeypatch):
    """pin 未含 abstain_reason 的舊版 → 不注入任何東西、不炸：理由記空（補救＝發版含欄版本）。"""
    _pack_env(monkeypatch)

    def _fake_load(pid: str, versions=None) -> dict:
        return {
            "title": pid,
            "system": f"SYS::{pid}",
            "user_template": "傾向：{POLARITY}\n{TEXT}",
            "schema": {"type": "object"},
        }

    monkeypatch.setattr(prompt_source, "load", _fake_load)

    def _fake_call(
        system, user, stage, model, *, schema=None, effort=None, label=None, cache_key=None
    ):
        assert "abstain_reason" not in (schema.get("required") or [])  # 舊版原樣送出，零注入
        assert "diagnostic_instructions" not in system
        return {"attributions": []}

    monkeypatch.setattr(prejudge, "_call", _fake_call)
    attrs = prejudge._resolve_attrs_multi({}, "整體不太行", "gpt-5-mini", 2, "negative")
    assert attrs == []

"""B0 診斷理由 overlay 測試（prompt_eval.py）：schema/system 動態注入 + 六域診斷並行。

覆蓋：
- `_diagnostic_domain_schema`/`_diagnostic_polarity_schema`：deep copy 不動原 schema、正確加欄。
- `domain_verdicts`：六域並行診斷（mock `prejudge._call`），命中域帶 reason、棄權域帶 abstain_reason，
  無論匹配與否六個域皆有交代；`_gate_attrs` 尾段閘門正確合流。

不需 LLM key / DB：`prejudge._call`、`prompt_source.load` 全 monkeypatch 固定（同 test_prejudge_pack.py
配方）；`prompt_source._domain_of`/`_domain_meta` 為真實函式（純字串/讀 domains.json，免 mock）。
"""

from __future__ import annotations

import copy

from app.judge import prejudge, prompt_source
from app.judge import prompt_eval as pe

# 域 prompt 代表性 schema 骨架（對齊 prompts/0N_C-N_*.md 的 Schema 節形狀）。
_DOMAIN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["attributions"],
    "properties": {
        "attributions": {
            "type": "array",
            "maxItems": 2,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["l2_code", "confidence", "summary", "evidence_quote"],
                "properties": {
                    "l2_code": {"type": "string", "enum": ["C-3-1", "C-3-2"]},
                    "confidence": {"type": "number"},
                    "summary": {"type": "array"},
                    "evidence_quote": {"type": "string"},
                },
            },
        }
    },
}
_POLARITY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["polarity", "sentiment"],
    "properties": {
        "polarity": {"type": "string"},
        "sentiment": {"type": "integer"},
    },
}


# ─────────────────────────── schema 動態加欄（純函式） ───────────────────────────
def test_diagnostic_domain_schema_adds_reason_and_abstain_reason():
    """域 schema：attributions[].reason 必填 + 頂層 abstain_reason 必填；原 schema 不被就地改動。"""
    original = copy.deepcopy(_DOMAIN_SCHEMA)
    out = pe._diagnostic_domain_schema(_DOMAIN_SCHEMA)
    # 原 schema 物件不受影響（deep copy，非就地改）
    assert _DOMAIN_SCHEMA == original
    item_schema = out["properties"]["attributions"]["items"]
    assert "reason" in item_schema["properties"]
    assert "reason" in item_schema["required"]
    assert "abstain_reason" in out["properties"]
    assert "abstain_reason" in out["required"]
    # 原有欄位/約束保留
    assert out["properties"]["attributions"]["maxItems"] == 2
    assert "l2_code" in item_schema["required"]


def test_diagnostic_polarity_schema_adds_reason():
    """極性 schema：reason 必填；原 schema 不受影響。"""
    original = copy.deepcopy(_POLARITY_SCHEMA)
    out = pe._diagnostic_polarity_schema(_POLARITY_SCHEMA)
    assert _POLARITY_SCHEMA == original
    assert "reason" in out["properties"]
    assert "reason" in out["required"]
    assert "polarity" in out["required"]  # 原欄位仍在


# ─────────────────────────── domain_verdicts（六域並行診斷） ───────────────────────────
def _diag_env(monkeypatch, *, amin: float = 0.0):
    """domain_verdicts 共用環境：非 stub + 面向白名單 + 政策 + 固定 prompt 載入。"""
    valid = {
        "C-3-1": ("supplier", "供應商履約", "人員服務"),
        "C-1-2": ("content", "商品內容", "行程資訊"),
    }
    monkeypatch.setattr(prejudge, "_l2_label_map", lambda: dict(valid))
    monkeypatch.setattr(
        prejudge, "_tiers", lambda: {"auto_accept": 0.8, "jury_low": 0.5, "jury_high": 0.7}
    )
    monkeypatch.setattr(
        prejudge,
        "_evidence_policy",
        lambda: {"require_quote_grounded": True, "attr_min_confidence": amin},
    )
    monkeypatch.setattr(prejudge, "_attr_effort", lambda: None)
    monkeypatch.setattr(prejudge, "_max_attributions", lambda: 2)

    def _fake_load(pid: str) -> dict:
        dom = prompt_source._domain_of(pid) or "content"
        return {
            "title": pid,
            "system": f"SYS::{pid}",
            "user_template": "傾向：{POLARITY}\n{TEXT}",
            "schema": copy.deepcopy(_DOMAIN_SCHEMA),
            "taxonomy": {
                "code": dom,
                "label": dom,
                "action": "x",
                "evidence_gated": dom == "supplier",
                "children": [{"code": "C-9-1", "label": "f"}],
            },
        }

    monkeypatch.setattr(prompt_source, "load", _fake_load)


def test_domain_verdicts_matched_and_abstained_all_six_present(monkeypatch):
    """六域皆有交代：僅 supplier 命中（帶 reason），其餘五域棄權（帶 abstain_reason）。"""
    _diag_env(monkeypatch)

    def _fake_call(system, user, stage, model, *, schema=None, effort=None):
        if system.startswith("SYS::03_C-3_supplier"):
            return {
                "attributions": [
                    {
                        "l2_code": "C-3-1",
                        "confidence": 0.9,
                        "summary": [{"lang": "zh-tw", "text": "態度差"}],
                        "evidence_quote": "導遊態度很差",
                        "reason": "評論明確抱怨導遊態度",
                    }
                ],
                "abstain_reason": "",
            }
        return {"attributions": [], "abstain_reason": "問題不屬本域"}

    monkeypatch.setattr(prejudge, "_call", _fake_call)
    gated, verdicts = pe.domain_verdicts(
        {"order_oid": "O1"}, "導遊態度很差全程臭臉", "gpt-5-mini", "negative"
    )

    assert len(verdicts) == 6  # 六域皆有交代（DOMAIN_PROMPT_IDS 固定 6 支）
    by_domain = {v["domain"]: v for v in verdicts}
    assert by_domain["supplier"]["matched"] is True
    assert by_domain["supplier"]["attributions"][0]["l2_code"] == "C-3-1"
    assert by_domain["supplier"]["attributions"][0]["reason"] == "評論明確抱怨導遊態度"
    assert by_domain["supplier"]["abstain_reason"] == ""
    # 其餘五域棄權且皆帶棄權理由（無論匹配與否都有交代）
    others = [v for d, v in by_domain.items() if d != "supplier"]
    assert len(others) == 5
    for v in others:
        assert v["matched"] is False
        assert v["attributions"] == []
        assert v["abstain_reason"] == "問題不屬本域"

    # 合流閘門：僅 supplier 一條存活
    assert len(gated) == 1
    assert gated[0]["l1_domain_code"] == "supplier"
    assert gated[0]["reason"] == "評論明確抱怨導遊態度"


def test_domain_verdicts_multi_domain_gated_by_confidence(monkeypatch):
    """兩域皆命中：_gate_attrs 依信心降冪排序（複用 production 同一套尾段規則，非另一份實作）。"""
    _diag_env(monkeypatch)

    def _fake_call(system, user, stage, model, *, schema=None, effort=None):
        if system.startswith("SYS::03_C-3_supplier"):
            return {
                "attributions": [
                    {
                        "l2_code": "C-3-1",
                        "confidence": 0.6,
                        "summary": [],
                        "evidence_quote": "態度差",
                        "reason": "供應商理由",
                    }
                ],
                "abstain_reason": "",
            }
        if system.startswith("SYS::01_C-1_content"):
            return {
                "attributions": [
                    {
                        "l2_code": "C-1-2",
                        "confidence": 0.85,
                        "summary": [],
                        "evidence_quote": "行程寫得不清楚",
                        "reason": "內容理由",
                    }
                ],
                "abstain_reason": "",
            }
        return {"attributions": [], "abstain_reason": "不屬本域"}

    monkeypatch.setattr(prejudge, "_call", _fake_call)
    gated, verdicts = pe.domain_verdicts({}, "行程寫得不清楚且態度差", "gpt-5-mini", "negative")

    assert [a["l1_domain_code"] for a in gated] == ["content", "supplier"]  # 0.85 > 0.6
    assert [a["reason"] for a in gated] == ["內容理由", "供應商理由"]
    assert sum(1 for v in verdicts if v["matched"]) == 2

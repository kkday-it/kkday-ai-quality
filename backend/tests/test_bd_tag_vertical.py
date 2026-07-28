"""bd_tag_vertical.pm_for / vertical_for / codes_for_vertical 測試（版本化規則 loader：monkeypatch db.get_rule_active）。

bd_tag_vertical 為可編輯版本化規則（rule_code='bd_tag_vertical'，走 judge_rule_versions，取代舊制
product_vertical），loader 即時讀 db.get_rule_active('bd_tag_vertical')；測試以 monkeypatch 注入
active 內容，不觸碰真 DB。
"""

from __future__ import annotations

from app.core import bd_tag_vertical, db


def _seed(monkeypatch, items: dict, **extra) -> None:
    """注入 db.get_rule_active('bd_tag_vertical') 回傳 {"items": ..., **extra}，模擬 active 版本內容。"""
    monkeypatch.setattr(
        db,
        "get_rule_active",
        lambda code: {"items": items, **extra} if code == "bd_tag_vertical" else None,
    )


def test_pm_for_and_vertical_for_returns_seeded_values(monkeypatch) -> None:
    """已注入的代碼回傳對應 PM/Vertical。"""
    _seed(
        monkeypatch,
        {
            "0006": {"note": "郊區行程", "pm": "Kiki", "vertical": "Tour"},
            "0003": {"note": "市內大眾運輸", "pm": "Bily", "vertical": "Trans"},
        },
    )
    assert bd_tag_vertical.pm_for("0006") == "Kiki"
    assert bd_tag_vertical.vertical_for("0006") == "Tour"
    assert bd_tag_vertical.pm_for("0003") == "Bily"
    assert bd_tag_vertical.vertical_for("0003") == "Trans"


def test_codes_for_vertical_returns_matching_codes(monkeypatch) -> None:
    """查詢某 Vertical 回傳所有對應代碼（多代碼可共用同一 Vertical）。"""
    _seed(
        monkeypatch,
        {
            "0001": {"note": "SIM Card", "pm": "Wanwan", "vertical": "COMM"},
            "0014": {"note": "Wi-Fi", "pm": "Wanwan", "vertical": "COMM"},
            "0006": {"note": "郊區行程", "pm": "Kiki", "vertical": "Tour"},
        },
    )
    assert sorted(bd_tag_vertical.codes_for_vertical("COMM")) == ["0001", "0014"]
    assert bd_tag_vertical.codes_for_vertical("Tour") == ["0006"]


def test_all_verticals_dedupes_and_sorts(monkeypatch) -> None:
    """all_verticals 回傳去重排序後的 Vertical 名稱清單。"""
    _seed(
        monkeypatch,
        {
            "0001": {"note": "", "pm": "Wanwan", "vertical": "COMM"},
            "0014": {"note": "", "pm": "Wanwan", "vertical": "COMM"},
            "0006": {"note": "", "pm": "Kiki", "vertical": "Tour"},
        },
    )
    assert bd_tag_vertical.all_verticals() == ["COMM", "Tour"]


def test_all_verticals_prefers_explicit_pool_over_derived(monkeypatch) -> None:
    """all_verticals 優先讀顯式 verticals 選項池（可含尚未指派給任何代碼的新值），非從 items 推導。"""
    _seed(
        monkeypatch,
        {"0006": {"note": "", "pm": "Kiki", "vertical": "Tour"}},
        verticals=["Tour", "Trans", "Charter"],  # Trans/Charter 尚無代碼指派，仍應出現
    )
    assert bd_tag_vertical.all_verticals() == ["Tour", "Trans", "Charter"]


def test_all_pms_returns_explicit_pool(monkeypatch) -> None:
    """all_pms 讀顯式 pms 選項池；缺版本回空 list。"""
    _seed(monkeypatch, {}, pms=["Kiki", "Bily", "Wanwan"])
    assert bd_tag_vertical.all_pms() == ["Kiki", "Bily", "Wanwan"]

    monkeypatch.setattr(db, "get_rule_active", lambda code: None)
    assert bd_tag_vertical.all_pms() == []


def test_unknown_code_or_vertical_and_empty_config_returns_empty(monkeypatch) -> None:
    """查詢不存在的代碼/Vertical，或無 active 版本（缺規則），一律安全回空值，不拋錯。"""
    _seed(monkeypatch, {"0001": {"note": "", "pm": "Wanwan", "vertical": "COMM"}})
    assert bd_tag_vertical.pm_for("9999") is None
    assert bd_tag_vertical.vertical_for("9999") is None
    assert bd_tag_vertical.codes_for_vertical("NotAVertical") == []

    monkeypatch.setattr(db, "get_rule_active", lambda code: None)
    assert bd_tag_vertical.all_items() == {}
    assert bd_tag_vertical.all_verticals() == []
    assert bd_tag_vertical.pm_for("0001") is None

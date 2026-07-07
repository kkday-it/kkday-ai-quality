"""多 model 聯合判決（ensemble.merge_votes / should_ensemble）純函式測試：合成 voter 結果，不呼叫 LLM。"""

from app.judge import ensemble


def _attr(l1: str, l3: str = "", conf: float = 0.6, l2: str = "") -> dict:
    """最小合成 attr dict（僅投票合併需要的欄位）。"""
    return {"l1_domain_code": l1, "l2_code": l2, "l3_code": l3, "confidence": conf}


def test_should_ensemble_gate():
    """低於 auto_accept 閾值才觸發；等於/高於不觸發（高信心直接採信省 token）。"""
    assert ensemble.should_ensemble(0.6, 0.8) is True
    assert ensemble.should_ensemble(0.85, 0.8) is False
    assert ensemble.should_ensemble(0.8, 0.8) is False


def test_merge_unanimous():
    """3 voter 全一致 → 一條聯合判決、agreement=1.0、不分歧、3 票攤平；conf＝各 voter 平均。"""
    vr = [
        {"model": m, "attrs": [_attr("content", "C-1-1-1", 0.6)]}
        for m in ("nano", "gemini", "seed")
    ]
    r = ensemble.merge_votes(vr)
    assert [a["l1_domain_code"] for a in r["merged"]] == ["content"]
    assert r["merged"][0]["l3_code"] == "C-1-1-1"
    assert r["agreement"] == 1.0
    assert r["disputed"] is False
    assert len(r["model_votes"]) == 3
    assert abs(r["merged"][0]["confidence"] - 0.6) < 1e-9


def test_merge_majority_with_minority_dispute():
    """2 voter 投 content、1 voter 投 supplier → 保留 content（2/3）、丟棄 supplier（1/3）、標記 disputed 需仲裁。"""
    vr = [
        {"model": "nano", "attrs": [_attr("content", "C-1-1-1", 0.6)]},
        {"model": "gemini", "attrs": [_attr("content", "C-1-1-1", 0.7)]},
        {"model": "seed", "attrs": [_attr("supplier", "C-2-1-1", 0.6)]},
    ]
    r = ensemble.merge_votes(vr)
    assert [a["l1_domain_code"] for a in r["merged"]] == ["content"]
    assert r["disputed"] is True
    assert round(r["agreement"], 4) == round(2 / 3, 4)
    assert len(r["model_votes"]) == 3


def test_merge_l3_mode_beats_high_conf():
    """同 L1 域全一致但 L3 分歧：L3 取眾數（2:1）勝過信心較高的少數 L3；域全一致故不算域分歧。"""
    vr = [
        {"model": "a", "attrs": [_attr("content", "C-1-1-1", 0.6)]},
        {"model": "b", "attrs": [_attr("content", "C-1-1-1", 0.6)]},
        {"model": "c", "attrs": [_attr("content", "C-1-1-2", 0.9)]},
    ]
    r = ensemble.merge_votes(vr)
    assert r["merged"][0]["l3_code"] == "C-1-1-1"
    assert r["disputed"] is False
    assert abs(r["merged"][0]["confidence"] - 0.7) < 1e-9  # avg(0.6,0.6,0.9)


def test_merge_multi_attribution_per_voter():
    """多歸因：每 voter 可投多個域；各域獨立統計一致度。"""
    vr = [
        {
            "model": "a",
            "attrs": [_attr("content", "C-1-1-1", 0.6), _attr("supplier", "C-2-1-1", 0.5)],
        },
        {
            "model": "b",
            "attrs": [_attr("content", "C-1-1-1", 0.7), _attr("supplier", "C-2-1-1", 0.6)],
        },
    ]
    r = ensemble.merge_votes(vr)
    doms = sorted(a["l1_domain_code"] for a in r["merged"])
    assert doms == ["content", "supplier"]  # 兩域皆 2/2 一致
    assert r["disputed"] is False
    assert r["agreement"] == 1.0


def test_merge_empty():
    """空輸入 → 空結果，不炸。"""
    assert ensemble.merge_votes([]) == {
        "merged": [],
        "agreement": 0.0,
        "disputed": False,
        "model_votes": [],
    }

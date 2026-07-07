"""標真值把關核心邏輯——級聯樹 / 路徑 label / LLM 評分器（stub）。

皆走 ai_judge 的 seed 檔 fallback（DB 無 active 版時讀 config/ai_judge/rule_C-*.json），故免測試庫。
"""

from app.core.judge_config import ai_judge
from app.judge import prejudge


def test_cascade_tree_nested_shape() -> None:
    """級聯樹回巢狀 {value,label,children}：6 個 L1 域，L1 下有 L2，L2 下有 L3。"""
    ai_judge.reload()
    tree = ai_judge.cascade_tree()
    assert len(tree) == 6  # content/quality/supplier/redemption/service/expectation
    l1 = tree[0]
    assert l1["value"] and l1["label"] and l1.get("children")
    l2 = l1["children"][0]
    assert l2["value"].startswith("C-") and l2["label"]
    assert all("value" in n and "label" in n for n in l1["children"])


def test_path_label_full_chain() -> None:
    """path_label：L1 域 code 回域名；L3 C-code 回「L1 › L2 › L3」完整路徑。"""
    ai_judge.reload()
    assert ai_judge.path_label("content") == "商品內容"
    leaf = ai_judge.cascade_tree()[0]["children"][0]["children"][0]["value"]
    path = ai_judge.path_label(leaf)
    assert path.count("›") == 2  # 三層路徑兩個分隔


def test_path_label_unknown_returns_empty() -> None:
    """未知 code → 空字串（呼叫端回退原 code）。"""
    ai_judge.reload()
    assert ai_judge.path_label("C-9-9-9") == ""


def test_proposed_label_path_falls_back_to_code() -> None:
    """prejudge._proposed_label_path 未知 code 回原 code（不炸）。"""
    ai_judge.reload()
    assert prejudge._proposed_label_path("nonexistent_xyz") == "nonexistent_xyz"


def test_score_true_label_stub_neutral() -> None:
    """無 LLM token（stub）→ 評分器回中性 0.5 + 說明，不呼叫外部。"""
    out = prejudge.score_true_label("跟照片有落差，票價蠻高的", "content", "gpt-5-nano")
    assert out["confidence"] == 0.5
    assert "stub" in out["reason"]

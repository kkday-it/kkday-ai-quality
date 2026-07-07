"""rule 反哺純函式測試（find_boundary_cases / update_node_canon）：合成資料，不碰 DB。"""

from app.judge import rule_refeed


def test_find_boundary_cases_watch_priority_and_examples():
    """誤判聚合成 (true,pred) 對；content↔supplier（watch）優先、收集 evidence 例句；判對/空不列。"""
    rows = [
        {"pred": "supplier", "true": "content", "evidence": "描述不符"},
        {"pred": "supplier", "true": "content", "evidence": "寫的和實際不一樣"},
        {"pred": "quality", "true": "content", "evidence": "訊號差"},
        {"pred": "content", "true": "content"},  # 判對 → 不列
        {"pred": "", "true": "content"},  # 空 → 不列
    ]
    r = rule_refeed.find_boundary_cases(rows)
    assert r[0] == {
        "true": "content",
        "pred": "supplier",
        "count": 2,
        "watch": True,
        "examples": ["描述不符", "寫的和實際不一樣"],
    }
    assert any(x["pred"] == "quality" and x["count"] == 1 and x["watch"] is False for x in r)
    assert all(x["true"] != x["pred"] for x in r)  # 判對不列


def test_find_boundary_cases_min_count_filter():
    """min_count 過濾偶發雜訊：只留出現 ≥ 門檻的域對。"""
    rows = [
        {"pred": "supplier", "true": "content"},
        {"pred": "supplier", "true": "content"},
        {"pred": "quality", "true": "content"},
    ]
    r = rule_refeed.find_boundary_cases(rows, min_count=2)
    assert len(r) == 1 and r[0]["pred"] == "supplier" and r[0]["count"] == 2


def test_update_node_canon_targets_only_canon_and_is_pure():
    """遞迴找 code 節點只改 canon，不動 allow/forbid/其他欄；深拷貝原物件不變。"""
    content = {
        "code": "C-1",
        "level": 1,
        "children": [
            {
                "code": "C-1-1",
                "level": 2,
                "children": [
                    {
                        "code": "C-1-1-4",
                        "level": 3,
                        "canon": "舊定義",
                        "allow": ["x"],
                        "forbid": ["y"],
                    },
                ],
            },
        ],
    }
    new, hit = rule_refeed.update_node_canon(content, "C-1-1-4", "新定義")
    assert hit is True
    leaf = new["children"][0]["children"][0]
    assert leaf["canon"] == "新定義"
    assert leaf["allow"] == ["x"] and leaf["forbid"] == ["y"]  # 其他欄不動
    # 原 content 未被就地改（純函式）
    assert content["children"][0]["children"][0]["canon"] == "舊定義"


def test_update_node_canon_miss():
    """找不到節點 → 回原樣 + False（不誤改）。"""
    content = {"code": "C-1", "canon": "a"}
    new, hit = rule_refeed.update_node_canon(content, "C-9-9-9", "z")
    assert hit is False and new == content

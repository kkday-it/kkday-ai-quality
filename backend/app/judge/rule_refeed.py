"""rule 反哺飛輪：用聯合判決（ensemble）+ 人工 true_label 的邊界誤判案例，精煉 rule node 的 canon。

飛輪：ensemble 分歧/判錯的案例＝rule 邊界模糊處 → 挑候選（`find_boundary_cases`）→ 人工/LLM 濃縮成更清楚的
canon → 寫回 DB active 版（`refeed_node_canon` → `save_rule_version` + `reload`）→ `_l3_catalog` 下次判決
自動取新 **完整 canon** → nano 判更準 → confidence 提高 → ensemble 觸發率遞減（token 遞減）。

⚠️ 邊界（誠實·以 code 為準）：canon 現已 **完整**注入判決 prompt（`_l3_catalog` 已移除舊 `[:40]` 截斷，
見 prejudge.py:322）→ 反哺 canon 完整生效；但 `allow/forbid/正反例` 仍未接進 prompt（只 Excel 匯出），
故本模組聚焦精煉 canon（立即有效）。canon 濃縮由呼叫端提供（真環境可接 LLM），本模組負責「挑候選」與
「安全寫回 + 熱重載」兩端純機制，故純函式部分可離線單元測。
"""
from __future__ import annotations

import copy
from typing import Any

# 重點監看的易混淆 L1 域對（kiki 指出 content↔supplier）；反哺候選優先排序用
_WATCH_PAIRS = frozenset({("content", "supplier"), ("supplier", "content")})


def find_boundary_cases(rows: list[dict[str, Any]], *, min_count: int = 1) -> list[dict[str, Any]]:
    """從「聯合判決 pred vs 人工 true_label」挑反哺候選：判錯的 (true→pred) 域對 + 佐證案例。

    誤判（pred≠true 且皆非空）聚合成 (true, pred) 對，計數 + 收集 evidence 例句；重點域對
    （content↔supplier）優先。供人工/LLM 據此精煉「該把哪個邊界寫進哪個 node 的 canon」。

    Args:
        rows: [{"pred": 聯合判決 L1 code, "true": 人工真值 L1 code, "evidence": 佐證原文(可空)}]。
        min_count: 出現次數 ≥ 此值才列為候選（過濾偶發雜訊）。

    Returns:
        [{"true", "pred", "count", "watch": bool, "examples": [evidence…最多5條]}]；
        watch 對優先、再依 count 降冪。
    """
    agg: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        pred, true = str(r.get("pred", "")), str(r.get("true", ""))
        if not pred or not true or pred == true:
            continue
        key = (true, pred)
        slot = agg.setdefault(key, {"true": true, "pred": pred, "count": 0, "examples": []})
        slot["count"] += 1
        ev = str(r.get("evidence", "")).strip()
        if ev and len(slot["examples"]) < 5:
            slot["examples"].append(ev)
    out = [
        {**v, "watch": (v["true"], v["pred"]) in _WATCH_PAIRS}
        for v in agg.values()
        if v["count"] >= min_count
    ]
    # 重點域對優先（watch True 排前），再依 count 降冪
    out.sort(key=lambda x: (not x["watch"], -x["count"]))
    return out


def update_node_canon(content: dict, code: str, new_canon: str) -> tuple[dict, bool]:
    """遞迴在 rule 樹（任意巢狀 dict/list）找 `code` 節點、改其 canon，回 (新 content 深拷貝, 是否命中)。

    只動 canon 一欄、不碰 allow/forbid/正反例/children（最小變更）；找不到節點回原樣 + False。
    深拷貝原 content 不就地改（呼叫端拿到的原物件保持不變）。
    """
    clone = copy.deepcopy(content)
    hit = [False]

    def _walk(obj: Any) -> None:
        if isinstance(obj, dict):
            if obj.get("code") == code and "canon" in obj:
                obj["canon"] = new_canon
                hit[0] = True
            for v in obj.values():
                _walk(v)
        elif isinstance(obj, list):
            for x in obj:
                _walk(x)

    _walk(clone)
    return clone, hit[0]


def refeed_node_canon(rule_code: str, node_code: str, new_canon: str, *, note: str = "", author: str = "") -> dict:
    """把精煉後的 canon 寫回某 rule node 的 DB active 版並熱重載（反哺飛輪的寫入端）。

    取 rule_code 的 active content（無 DB 版則取 default）→ 遞迴改 node_code 的 canon → save 新 active 版
    → reload ai_judge 快取（`_l3_catalog` 下次判決即取新 canon，不需改 code/重啟）。

    Args:
        rule_code: 頂層 rule 檔 code（如 C-1；node 屬其樹）。
        node_code: 要精煉的節點 code（如 C-1-1-4）。
        new_canon: 精煉後的 canon 定義（呼叫端提供；真環境可由 LLM 據 boundary_cases 濃縮）。
        note/author: 版本備註 / 作者（存 judge_rule_versions audit）。

    Returns:
        {rule_code, version, node_code, updated: bool}；node 未命中則不寫版本、updated=False。

    Raises:
        ValueError: rule_code 無 active 也無 default 內容。
    """
    from app.core import db
    from app.core.judge_config import ai_judge

    content = db.get_rule_active(rule_code) or db.default_rule_content(rule_code)
    if not content:
        raise ValueError(f"rule_code 無 active 也無 default 內容：{rule_code}")
    new_content, updated = update_node_canon(content, node_code, new_canon)
    if not updated:
        return {"rule_code": rule_code, "version": None, "node_code": node_code, "updated": False}
    saved = db.save_rule_version(rule_code, new_content, note=note or f"反哺精煉 {node_code} canon", author=author)
    ai_judge.reload()  # 熱重載：_l3_catalog 下次判決取新 canon 生效
    return {**saved, "node_code": node_code, "updated": True}

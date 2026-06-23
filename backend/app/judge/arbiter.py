"""L3 仲裁（純程式，確定性）：classify × adequacy → verdict + 信心度。

內容證據（adequacy）凌駕客訴語氣。對應 specs/04 仲裁表。
"""

from __future__ import annotations


def reconcile(classify: dict, adequacy: dict | None) -> tuple[str, float]:
    cv = classify.get("tentative_verdict", "escalate_ops")
    dim = classify.get("dimension", "non_content")

    if dim == "non_content" or cv == "escalate_ops":
        return "escalate_ops", float(classify.get("confidence", 0.5))

    if adequacy is None:
        # 無可歸因欄位
        return "customer_misread", 0.4

    st = adequacy.get("status", "unclear")
    if st in ("missing", "field_empty"):
        return "content_missing", 0.9
    if st == "contradictory":
        return "real_config_issue", 0.9
    if st == "unclear":
        return "content_unclear", 0.85
    if st == "adequate":
        return "customer_misread", 0.8  # 內容其實清楚 → 降級
    return "content_unclear", 0.6

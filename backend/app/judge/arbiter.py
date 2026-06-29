"""L3 仲裁（純程式，確定性）：classify × adequacy → verdict + 信心度。

內容證據（adequacy）凌駕客訴語氣。對應 specs/04 仲裁表。
"""

from __future__ import annotations


def reconcile(
    classify: dict,
    adequacy: dict | None,
    machine_findings: list[dict] | None = None,
    rule_hits: list[dict] | None = None,
) -> tuple[str, float]:
    cv = classify.get("tentative_verdict", "escalate_ops")
    dim = classify.get("dimension", "non_content")
    rules = {f.get("rule") for f in (machine_findings or [])}

    if dim == "non_content" or cv == "escalate_ops":
        return "escalate_ops", float(classify.get("confidence", 0.5))

    # 法典確定性層（零 LLM·codex.scan_misplacement）：錯位行銷/成團關鍵字（R5-2/R5-3）
    # → 直接用該規則 verdict_hint（real_config_issue），不必等 LLM
    if rule_hits:
        return rule_hits[0].get("verdict_hint", "real_config_issue"), 0.9

    # 確定性閘門（零 LLM·vendored machine_checks）：欄位空 → 缺漏，不必等 LLM
    if "empty_output" in rules:
        return "content_missing", 0.9

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
        # 內容看似清楚，但有確定性瑕疵（禁詞/促銷標籤/結構缺失）→ 非純誤解
        if rules & {
            "forbidden_terms",
            "product_name_promo_bracket",
            "description_markdown_structure",
        }:
            return "content_unclear", 0.7
        return "customer_misread", 0.8  # 內容其實清楚 → 降級
    return "content_unclear", 0.6

"""L4 行動診斷（純程式）：verdict → recommended_action + writer_handoff（防幻覺）。

對應 specs/04 diagnose mapping。
"""

from __future__ import annotations

# verdict → (recommended_action, writer_handoff 預設)
_ACTION_MAP: dict[str, tuple[str, bool]] = {
    "real_config_issue": ("fix_contradiction", True),
    "content_missing": ("add_missing_info", False),  # 防幻覺：缺事實一律 False
    "content_unclear": ("clarify_wording", True),
    "customer_misread": ("no_action", False),
    "escalate_ops": ("escalate_ops", False),
}

# 現有 writer 只產這 3 欄
_WRITER_FIELDS = {"prod_name", "prod_feature", "prod_summary"}

_DETAIL = {
    "real_config_issue": "設定自相矛盾，請修正欄位內容使其一致。",
    "content_missing": "缺少必要事實，請 PM 補入真實資訊（writer 不可生成）。",
    "content_unclear": "描述模糊易誤解，建議改寫使其清楚。",
    "customer_misread": "內容其實已清楚，屬呈現/UX 洞察，無需改內容。",
    "escalate_ops": "非內容類（服務/出貨等），轉營運/客服處理。",
}


def build_action(verdict: str, classify: dict, ground_truth: str = "") -> tuple[str, str, bool]:
    action, handoff_default = _ACTION_MAP.get(verdict, ("no_action", False))
    field = classify.get("suspected_field", "none")
    # 防幻覺鐵則：content_missing 強制 False；其餘需 verdict 允許 + 欄位 ∈ writer 3 欄
    handoff = (
        handoff_default
        and verdict in ("content_unclear", "real_config_issue")
        and field in _WRITER_FIELDS
    )
    detail = _DETAIL.get(verdict, "")
    if ground_truth:
        detail += f"（ground truth：{ground_truth[:80]}）"
    return action, detail, handoff

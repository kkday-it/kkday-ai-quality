"""L3 充分度檢查（第二意見，只看商品原文、不採信客訴歸咎）。

stub：簡化啟發式；real：LLM（key 到位）。
"""

from __future__ import annotations

from app.judge.llm import client


def check(field_text: str, dimension: str, concern: str) -> dict:
    if client.is_stub():
        return _stub(field_text, concern)
    return _real(field_text, dimension, concern)


def _stub(field_text: str, concern: str) -> dict:
    if not field_text:
        return {"status": "field_empty", "evidence": "（該欄位未取得原文）", "reason": "stub"}
    # 賣點 vs 客訴衝突偵測：欄位宣稱「贈送/含」某項，但客訴說「沒有/關閉/取消」→ 標題與實況不符
    claim = any(k in field_text for k in ["贈送", "含", "提供", "免費"])
    deny = any(k in concern for k in ["沒有", "沒贈送", "關閉", "取消", "不實", "舊資料"])
    if claim and deny:
        return {"status": "unclear", "evidence": field_text[:80], "reason": "stub：賣點與客訴實況不符"}
    return {"status": "unclear", "evidence": field_text[:80], "reason": "stub：保守判 unclear"}


def _real(field_text: str, dimension: str, concern: str) -> dict:
    system = (
        "你是商品內容稽核員，只看欄位原文、不採信客訴歸咎。判斷此欄位是否清楚正確交代了"
        f"「{dimension}」在此 concern 下該有的資訊。輸出 JSON：status "
        "(adequate|unclear|missing|contradictory|field_empty), evidence, reason。"
    )
    user = f"欄位原文：\n{field_text}\n\nconcern：{concern}"
    return client.chat_json(system, user)

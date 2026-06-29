"""L2 分類：客訴/差評 → dimension + 疑似欄位 + 初判（只看客訴）。

stub：關鍵字啟發式（零 key 走通）；real：LLM（key 到位，注入 8 dimension 定義）。
"""

from __future__ import annotations

from app.core.schema import NormalizedTicket
from app.judge.llm import client

# 強服務負面詞（→ non_content / escalate_ops）
SERVICE_KW = [
    "催",
    "遲到",
    "羞辱",
    "公審",
    "素質",
    "沒下車",
    "沒導覽",
    "不專業",
    "斥責",
    "煩死",
    "趕人",
    "催促",
    "態度",
]

# 內容問題規則（關鍵字 → dimension, suspected_field）
CONTENT_RULES: list[tuple[list[str], str, str]] = [
    (
        ["纜車", "贈送", "商標", "logo", "不實", "騙", "舊資料", "標題", "招徠"],
        "承諾與SLA",
        "prod_name",
    ),
    (["憑證", "標示", "兌換", "換票", "使用方法"], "使用兌換", "pkg_desc"),
    (["費用", "價格", "退費", "加購", "收費"], "費用資訊", "prod_summary"),
    (["倉促", "趕", "小時", "結束", "提早", "停留", "時間"], "行程流程", "prod_schedules"),
]


def classify(ticket: NormalizedTicket) -> dict:
    if client.is_stub():
        return _stub(ticket.comment)
    return _real(ticket)


def _stub(comment: str) -> dict:
    summary = comment[:60]
    # 內容強訊號優先（纜車/憑證/費用），其次服務，其次行程時間
    for kws, dim, field in CONTENT_RULES[:3]:
        if any(k in comment for k in kws):
            return _c(dim, field, "content_unclear", summary, 0.6)
    if any(k in comment for k in SERVICE_KW):
        return _c("non_content", "none", "escalate_ops", summary, 0.7)
    kws, dim, field = CONTENT_RULES[3]  # 行程時間
    if any(k in comment for k in kws):
        return _c(dim, field, "content_unclear", summary, 0.55)
    return _c("non_content", "none", "escalate_ops", summary, 0.4)


def _c(dim: str, field: str, verdict: str, summary: str, conf: float) -> dict:
    return {
        "dimension": dim,
        "suspected_field": field,
        "tentative_verdict": verdict,
        "problem_summary": summary,
        "confidence": conf,
        "is_primary": True,
    }


def _real(ticket: NormalizedTicket) -> dict:
    """key 到位：system 注入 8 dimension + verdict 定義；只看客訴，輸出 JSON。"""
    system = (
        "你是旅遊電商客訴分析員。讀客訴，分類到 8 dimension（商品定位/行程流程/費用資訊/"
        "集合資訊/使用兌換/成團條件/限制與風險/承諾與SLA）或 non_content（服務/出貨）。"
        "只看客訴不看商品原文。輸出 JSON：dimension, suspected_field, tentative_verdict, "
        "problem_summary, confidence, is_primary。"
    )
    return client.chat_json(system, ticket.comment, stage="classify")

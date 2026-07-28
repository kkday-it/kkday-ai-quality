"""售後根因 Prompt 調試台人工評判案例庫存取。

一列＝一個被人工判過對錯的 session（對話原文 + AI 當時判的 + 人標的正解 + 修改建議）。
兩個消費端：`app.judge.prompt_reviser`（餵給 AI 當改寫證據）與回歸重跑（拿舊案例驗新 Prompt）。

列表與明細刻意分兩支：對話原文動輒上萬字，案例累積到數十筆時全量拉取會讓列表請求變重，
所以 `list_*` 只回摘要（原文截斷成預覽 + 長度），真要全文時由呼叫端以 id 走 `fetch_*`。
"""

from __future__ import annotations

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select

from app.core.db import tables as T

# 列表預覽截斷長度：夠認出是哪一則對話即可，全文走 fetch_prompt_debug_reviews
_PREVIEW_CHARS = 200


def insert_prompt_debug_review(
    conversation: str,
    ai_output: dict,
    corrections: dict,
    confirmed: list[str] | None = None,
    comment: str = "",
    prompt_version: str = "",
    model: str = "",
    reviewer: str = "",
) -> int:
    """存一則人工評判案例。

    Args:
        conversation: 當時的調試文本原文。
        ai_output: AI 判定的全部欄位（原樣存，不過濾）。
        corrections: 人標的正解 `{欄名: 正解值}`；只放被標錯的欄，全對傳 `{}`。
        confirmed: 人明確標「對」的欄名；回歸時這些欄不准變（沒出現在兩者的欄＝沒看過，不計分）。
        comment: 人寫的修改建議（可空）。
        prompt_version: 當時線上 Prompt 版本名；空＝送出前臨時編輯過。
        model: 當時用的模型。
        reviewer: 評判人 email。

    Returns:
        新案例的 id。
    """
    r = T.prompt_debug_reviews
    stmt = (
        r.insert()
        .values(
            conversation=conversation,
            ai_output=ai_output,
            corrections=corrections,
            confirmed=list(confirmed or []),
            comment=comment,
            prompt_version=prompt_version,
            model=model,
            reviewer=reviewer,
        )
        .returning(r.c.id)
    )
    with T.get_engine().begin() as c:
        return int(c.execute(stmt).scalar_one())


def list_prompt_debug_reviews() -> list[dict]:
    """全部案例摘要（新→舊）：不含對話全文，改回 `conversation_preview` + `conversation_chars`。

    `corrections` / `ai_output` 照原樣回（前端列表要顯示標錯了哪幾欄、AI 當時判什麼）。
    """
    r = T.prompt_debug_reviews
    stmt = select(
        r.c.id,
        func.left(r.c.conversation, _PREVIEW_CHARS).label("conversation_preview"),
        func.length(r.c.conversation).label("conversation_chars"),
        r.c.ai_output,
        r.c.corrections,
        r.c.confirmed,
        r.c.comment,
        r.c.prompt_version,
        r.c.model,
        r.c.reviewer,
        r.c.created_at,
    ).order_by(r.c.created_at.desc(), r.c.id.desc())
    with T.get_engine().connect() as c:
        return [dict(row) for row in c.execute(stmt).mappings()]


def fetch_prompt_debug_reviews(ids: list[int]) -> list[dict]:
    """依 id 取案例全文（含 conversation）；順序照傳入 ids，找不到的 id 直接略過。

    餵 AI 改寫與回歸重跑都只針對使用者勾選的少數幾筆，故不做分頁。
    """
    if not ids:
        return []
    r = T.prompt_debug_reviews
    stmt = select(
        r.c.id,
        r.c.conversation,
        r.c.ai_output,
        r.c.corrections,
        r.c.confirmed,
        r.c.comment,
        r.c.prompt_version,
        r.c.model,
        r.c.reviewer,
        r.c.created_at,
    ).where(r.c.id.in_(ids))
    with T.get_engine().connect() as c:
        by_id = {int(row["id"]): dict(row) for row in c.execute(stmt).mappings()}
    return [by_id[i] for i in ids if i in by_id]


def delete_prompt_debug_review(review_id: int) -> bool:
    """刪一則案例；回是否確實刪到一列（否＝id 不存在）。"""
    r = T.prompt_debug_reviews
    with T.get_engine().begin() as c:
        res = c.execute(sa_delete(r).where(r.c.id == review_id))
    return bool(res.rowcount)

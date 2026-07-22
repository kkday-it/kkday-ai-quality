"""初判 Prompt 草稿存取（prompt_polarity / prompt_C-1~6 每 rule_code 一份共享草稿）。

草稿＝未入庫的編輯中 prompt 內容：沙盒可直接送測（雙跑對比），驗證滿意後由呼叫端走
`save_rule_version` 入庫成新 active 版並刪草稿；與 judge_rule_versions 完全分離，
版本表維持「存檔即 active」單一語意。併發策略＝last-write-wins（單團隊調適工具，
不做鎖；updated_by/updated_at 供前端顯示編輯線索）。
"""

from __future__ import annotations

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.db import tables as T


def get_prompt_draft(rule_code: str) -> dict | None:
    """取某 prompt 的草稿（{content, base_version, updated_by, updated_at}）；無草稿回 None。"""
    d = T.prompt_drafts
    stmt = select(d.c.content, d.c.base_version, d.c.updated_by, d.c.updated_at).where(
        d.c.rule_code == rule_code
    )
    with T.get_engine().connect() as c:
        row = c.execute(stmt).mappings().first()
    return dict(row) if row else None


def list_prompt_drafts() -> list[dict]:
    """列所有存在草稿的 prompt（rule_code/base_version/updated_by/updated_at，不含 content）——
    供前端 picker 一次拉取草稿存在狀態，免逐 code 輪詢。"""
    d = T.prompt_drafts
    stmt = select(d.c.rule_code, d.c.base_version, d.c.updated_by, d.c.updated_at).order_by(
        d.c.rule_code
    )
    with T.get_engine().connect() as c:
        return [dict(r) for r in c.execute(stmt).mappings()]


def upsert_prompt_draft(
    rule_code: str, content: dict, base_version: int, updated_by: str = ""
) -> None:
    """寫入/覆蓋某 prompt 的草稿（last-write-wins；updated_at 以 DB now() 刷新）。"""
    d = T.prompt_drafts
    stmt = pg_insert(d).values(
        rule_code=rule_code,
        content=content,
        base_version=base_version,
        updated_by=updated_by,
        updated_at=func.now(),
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[d.c.rule_code],
        set_={
            "content": stmt.excluded.content,
            "base_version": stmt.excluded.base_version,
            "updated_by": stmt.excluded.updated_by,
            "updated_at": func.now(),
        },
    )
    with T.get_engine().begin() as c:
        c.execute(stmt)


def delete_prompt_draft(rule_code: str) -> bool:
    """刪除某 prompt 的草稿（入庫採納後清理／手動捨棄）。回是否確實刪到一列。"""
    d = T.prompt_drafts
    with T.get_engine().begin() as c:
        res = c.execute(sa_delete(d).where(d.c.rule_code == rule_code))
    return bool(res.rowcount)

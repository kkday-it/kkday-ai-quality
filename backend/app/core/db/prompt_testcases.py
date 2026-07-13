"""邊界測試集（prompt_testcases）：B3——CSV 上傳 / 手動新增 / 分歧一鍵入集三來源同表的純資料層。

驗證（gold_l1/gold_l2/expected_polarity 是否合法）不在本模組——那是業務規則，見
`app.judge.prompt_testcases.validate_row`；本模組只管存取，呼叫端須先驗證過再寫入。
"""

from __future__ import annotations

import uuid

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy import insert as sa_insert
from sqlalchemy import update as sa_update

from app.core.db import tables as T


def insert_prompt_testcase(row: dict) -> str:
    """新增單筆測試 case（手動新增 / 分歧一鍵入集共用），回傳新建 id。"""
    tc_id = f"tc_{uuid.uuid4().hex}"
    with T.get_engine().begin() as c:
        c.execute(
            sa_insert(T.prompt_testcases).values(
                id=tc_id,
                text=row["text"],
                gold_l1=row["gold_l1"],
                gold_l2=row.get("gold_l2") or None,
                expected_polarity=row.get("expected_polarity") or None,
                note=row.get("note") or "",
                tags=row.get("tags") or [],
                enabled=True,
                created_by=row.get("created_by", ""),
            )
        )
    return tc_id


def bulk_insert_prompt_testcases(rows: list[dict]) -> dict:
    """CSV 批量插入（跳過與既有 text 完全重複者，含批次內互相重複）→ {inserted, skipped}。

    Args:
        rows: 已驗證正規化的 row 清單（見 `app.judge.prompt_testcases.parse_csv`）。
    """
    tc = T.prompt_testcases
    with T.get_engine().begin() as c:
        existing = {r[0] for r in c.execute(select(tc.c.text)).all()}
        seen: set[str] = set()
        to_insert: list[dict] = []
        skipped = 0
        for row in rows:
            t = row["text"]
            if t in existing or t in seen:
                skipped += 1
                continue
            seen.add(t)
            to_insert.append(
                {
                    "id": f"tc_{uuid.uuid4().hex}",
                    "text": t,
                    "gold_l1": row["gold_l1"],
                    "gold_l2": row.get("gold_l2") or None,
                    "expected_polarity": row.get("expected_polarity") or None,
                    "note": row.get("note") or "",
                    "tags": row.get("tags") or [],
                    "enabled": True,
                    "created_by": row.get("created_by", ""),
                }
            )
        if to_insert:
            c.execute(sa_insert(tc), to_insert)
    return {"inserted": len(to_insert), "skipped": skipped}


def list_prompt_testcases(
    gold_l1: str | None = None,
    tags: list[str] | None = None,
    enabled: bool | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """測試集列表（篩 gold_l1/enabled 於 SQL；tags 資料量小改於 Python 後置篩選 → 分頁）→ {total, items}。"""
    tc = T.prompt_testcases
    stmt = select(tc)
    cnt = select(func.count()).select_from(tc)
    if gold_l1:
        stmt = stmt.where(tc.c.gold_l1 == gold_l1)
        cnt = cnt.where(tc.c.gold_l1 == gold_l1)
    if enabled is not None:
        stmt = stmt.where(tc.c.enabled == enabled)
        cnt = cnt.where(tc.c.enabled == enabled)
    stmt = stmt.order_by(tc.c.created_at.desc())
    with T.get_engine().connect() as c:
        if tags:
            rows = [_serialize(dict(r)) for r in c.execute(stmt).mappings().all()]
            wanted = set(tags)
            rows = [r for r in rows if wanted & set(r.get("tags") or [])]
            total = len(rows)
            items = rows[offset : offset + limit]
        else:
            total = int(c.execute(cnt).scalar() or 0)
            items = [
                _serialize(dict(r))
                for r in c.execute(stmt.limit(limit).offset(offset)).mappings().all()
            ]
    return {"total": total, "items": items}


def get_prompt_testcase(tc_id: str) -> dict | None:
    """單筆測試 case（供 PATCH 合併驗證用）；不存在回 None。"""
    with T.get_engine().connect() as c:
        row = (
            c.execute(select(T.prompt_testcases).where(T.prompt_testcases.c.id == tc_id))
            .mappings()
            .first()
        )
    return _serialize(dict(row)) if row is not None else None


def update_prompt_testcase(tc_id: str, patch: dict) -> bool:
    """局部更新（白名單欄位）→ 是否命中更新到列。"""
    allowed = {"text", "gold_l1", "gold_l2", "expected_polarity", "note", "tags", "enabled"}
    values = {k: v for k, v in patch.items() if k in allowed}
    if not values:
        return False
    with T.get_engine().begin() as c:
        res = c.execute(
            sa_update(T.prompt_testcases).where(T.prompt_testcases.c.id == tc_id).values(**values)
        )
    return res.rowcount > 0


def delete_prompt_testcase(tc_id: str) -> bool:
    """刪除單筆測試 case → 是否命中刪除。"""
    with T.get_engine().begin() as c:
        res = c.execute(sa_delete(T.prompt_testcases).where(T.prompt_testcases.c.id == tc_id))
    return res.rowcount > 0


def enabled_testcases(gold_l1: str | None = None) -> list[dict]:
    """啟用中的測試 case（供 `prompt_eval.run_eval(source="mock")` 抽樣）。

    Args:
        gold_l1: 給定則只取該域；None 取全部（域評測需要「他域案例＝棄權分母」，故域 prompt 抽樣
            時仍應呼叫本函式不帶 gold_l1，於呼叫端依 row["gold_l1"] 自行分正例/棄權）。
    """
    tc = T.prompt_testcases
    stmt = select(tc).where(tc.c.enabled.is_(True))
    if gold_l1:
        stmt = stmt.where(tc.c.gold_l1 == gold_l1)
    with T.get_engine().connect() as c:
        rows = c.execute(stmt).mappings().all()
    return [_serialize(dict(r)) for r in rows]


def _serialize(row: dict) -> dict:
    """datetime 欄 → ISO 字串（對齊專案時間欄以字串出 API 的慣例）。"""
    v = row.get("created_at")
    row["created_at"] = v.isoformat() if v is not None and hasattr(v, "isoformat") else v
    return row

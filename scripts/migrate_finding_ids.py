#!/usr/bin/env python3
"""一次性資料遷移：舊 finding_id 命名 `<item_id>-f1` → 新單軸引擎 `fd_<item_id>`。

背景：去 verdict 軸重寫後，新引擎（app/judge/prejudge）以 `fd_<item_id>` 為 finding_id 冪等
upsert（一 item 一 finding）；但舊管線寫的是 `<item_id>-f1`，命名不同 → 重判時新結果不覆蓋舊的，
`list_problems`（intake ⟕ judgments on item_id）遂對同一 item 發散成兩列。

遷移規則（新覆蓋舊、不清空判定結果）：
- 同 item 已有 `fd_<item_id>`（新引擎已重判過）→ 刪舊 `-f1`（新的直接覆蓋舊的）。
- 同 item 僅舊 `-f1` → rename column + data JSON 內 finding_id 為 `fd_<item_id>`（保留判決結果）。

冪等：跑完全部為 `fd_`；再次執行無舊格式列 → no-op。target 恆為 `fd_` + judgments.item_id
（用 item_id 欄組，不 parse finding_id 字串，最穩）。

用法：python scripts/migrate_finding_ids.py [--dry-run]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "backend"))

from sqlalchemy import delete, select, update  # noqa: E402

from app.core import tables as T  # noqa: E402

_FD = "fd_"


def _plan(conn) -> tuple[list[tuple[str, str, str]], list[str]]:
    """掃描 judgments，產出 (renames, deletes)。

    Returns:
        renames: [(old_finding_id, new_finding_id, new_data_json)]（僅舊格式且該 item 無 fd_）。
        deletes: [old_finding_id]（該 item 已有 fd_，舊的直接刪）。
    """
    jg = T.judgments
    # 已有 fd_ 的 item_id（新引擎已重判 → 舊的要刪）
    fd_items = {
        r[0] for r in conn.execute(select(jg.c.item_id).where(jg.c.finding_id.like("fd\\_%", escape="\\")))
    }
    renames: list[tuple[str, str, str]] = []
    deletes: list[str] = []
    rows = conn.execute(
        select(jg.c.finding_id, jg.c.item_id, jg.c.data).where(
            ~jg.c.finding_id.like("fd\\_%", escape="\\")
        )
    ).mappings()
    for r in rows:
        old_fid = r["finding_id"]
        item_id = r["item_id"]
        if not item_id:
            continue  # 無 item_id 無法組 target，跳過（理論上不存在）
        if item_id in fd_items:
            deletes.append(old_fid)
            continue
        target = _FD + item_id
        # 同步改 data JSON 內 finding_id（accuracy 報表讀 data.finding_id）
        new_data = r["data"] or "{}"
        try:
            d = json.loads(new_data)
            d["finding_id"] = target
            new_data = json.dumps(d, ensure_ascii=False)
        except (ValueError, TypeError):
            pass  # data 壞掉不阻斷 rename（column 為權威）
        renames.append((old_fid, target, new_data))
    return renames, deletes


def main() -> None:
    """執行遷移（--dry-run 僅印計畫不寫入）。"""
    dry = "--dry-run" in sys.argv
    jg = T.judgments
    eng = T.get_engine()
    with eng.begin() as conn:
        renames, deletes = _plan(conn)
        print(f"計畫：rename {len(renames)} 筆（保留判決）· delete {len(deletes)} 筆（舊，新已覆蓋）")
        if dry:
            for old, new, _ in renames[:5]:
                print(f"  rename {old} → {new}")
            for old in deletes[:5]:
                print(f"  delete {old}")
            print("（--dry-run，未寫入）")
            return
        for old_fid, new_fid, new_data in renames:
            conn.execute(
                update(jg).where(jg.c.finding_id == old_fid).values(finding_id=new_fid, data=new_data)
            )
        if deletes:
            conn.execute(delete(jg).where(jg.c.finding_id.in_(deletes)))

    # 驗證結果
    with eng.connect() as conn:
        from sqlalchemy import func  # noqa: PLC0415

        total = conn.execute(select(func.count()).select_from(jg)).scalar()
        remaining_old = conn.execute(
            select(func.count()).where(~jg.c.finding_id.like("fd\\_%", escape="\\"))
        ).scalar()
    print(f"完成：judgments {total} 筆 · 剩餘非 fd_ = {remaining_old}（應為 0）")


if __name__ == "__main__":
    main()

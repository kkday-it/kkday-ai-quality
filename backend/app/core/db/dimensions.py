"""值域主檔——業務可於「🗂️ 分類與選項」抽屜維護的參照資料。

多軸共用一張表（以 `dimension_code` 判別），與 `judge_rule_version_lst` 用 `rule_code` 判別是
同一個慣例：欄形相同的值域共用一表，避免每加一軸就多一套 migration／API／畫面。
**目前僅 `note_type`（備註互動類型）一軸**——責任方／嚴重度／建議行動三軸零消費者，
已於 migration `f2a91c7b4d08` 清退。

**檔案是 seed、DB 是 live**（同 bd_tag_vertical / source_mapping 的既有慣例）：空庫首次讀取時自
`config/ai_judge/attribution_dimension.json` 冪等灌入，之後以 DB 為準。

**沒有刪除函式**是刻意的：`item_code` 會被歷史判決引用，硬刪會讓那些列的顯示欄變空白。停用一律
走 `is_active=false`——選單裡不再出現，但既有資料仍解析得到 label。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.db import tables as T
from app.core.paths import AI_JUDGE_DIR

# 值域各軸的顯示順序（前端下拉分組用）。
#
# 目前只有 `note_type`（備註的互動類型）一軸。表以 `dimension_code` 判別，保留多軸能力：
# 日後新增值域（欄形同為 code / label / 說明 / 排序 / 啟用 / 稽核）直接加一個 code 即可，
# 不必再開一張表與一套 migration / API / 維護畫面。
DIMENSION_CODES = ("note_type",)

_WIRE = (
    "attribution_dimension_oid",
    "dimension_code",
    "item_code",
    "item_label",
    "item_desc",
    "sort_order",
    "is_active",
)


def _seed_rows() -> list[dict[str, Any]]:
    """讀種子檔 → 落庫列形狀；檔案缺失或壞掉回空清單（不阻斷啟動）。"""
    try:
        raw = json.loads((AI_JUDGE_DIR / "attribution_dimension.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows: list[dict[str, Any]] = []
    for dim in DIMENSION_CODES:
        for i, it in enumerate(raw.get(dim, {}).get("items", [])):
            rows.append(
                {
                    "dimension_code": dim,
                    "item_code": it["code"],
                    "item_label": it["label"],
                    "item_desc": it.get("desc"),
                    "sort_order": i,
                    "create_user": "system",
                }
            )
    return rows


def seed_dimensions_from_file() -> int:
    """空庫首次啟動時灌入預設值域（冪等：已存在的 (軸, code) 不動）。回實際新增列數。"""
    rows = _seed_rows()
    if not rows:
        return 0
    d = T.attribution_dimensions
    with T.get_engine().begin() as c:
        before = c.execute(select(func.count()).select_from(d)).scalar() or 0
        c.execute(
            pg_insert(d)
            .values(rows)
            .on_conflict_do_nothing(index_elements=["dimension_code", "item_code"])
        )
        after = c.execute(select(func.count()).select_from(d)).scalar() or 0
    return int(after - before)


def list_dimensions(include_inactive: bool = False) -> dict[str, list[dict]]:
    """各軸值域 → {dimension_code: [選項…]}（一次取齊，表單只打一發請求）。

    Args:
        include_inactive: True＝連停用項也回（維護畫面用）；False＝只回可選項（表單下拉用）。
    """
    d = T.attribution_dimensions
    stmt = select(*[d.c[k] for k in _WIRE]).order_by(
        d.c.dimension_code, d.c.sort_order, d.c.item_code
    )
    if not include_inactive:
        stmt = stmt.where(d.c.is_active)
    with T.get_engine().connect() as c:
        rows = c.execute(stmt).mappings().all()
    out: dict[str, list[dict]] = {code: [] for code in DIMENSION_CODES}
    for r in rows:
        out.setdefault(r["dimension_code"], []).append(dict(r))
    return out


def save_dimension_item(values: dict[str, Any], *, author: str) -> dict:
    """新增或更新單一值域項（upsert on (dimension_code, item_code)）→ 回落庫後的列。

    Raises:
        ValueError: 軸別不合法或必填欄缺漏（router 轉 422）。
    """
    dim = str(values.get("dimension_code") or "")
    code = str(values.get("item_code") or "").strip()
    label = str(values.get("item_label") or "").strip()
    if dim not in DIMENSION_CODES:
        raise ValueError(f"值域軸別須為 {' / '.join(DIMENSION_CODES)} 之一（收到 {dim!r}）")
    if not code:
        raise ValueError("項目機器碼必填（且落庫後不應再改——會改變歷史判決的語義）")
    if not label:
        raise ValueError("項目顯示名必填")

    d = T.attribution_dimensions
    row = {
        "dimension_code": dim,
        "item_code": code,
        "item_label": label,
        "item_desc": values.get("item_desc"),
        "sort_order": int(values.get("sort_order") or 0),
        "is_active": bool(values.get("is_active", True)),
    }
    stmt = pg_insert(d).values(**row, create_user=author)
    stmt = stmt.on_conflict_do_update(
        index_elements=["dimension_code", "item_code"],
        set_={
            "item_label": stmt.excluded.item_label,
            "item_desc": stmt.excluded.item_desc,
            "sort_order": stmt.excluded.sort_order,
            "is_active": stmt.excluded.is_active,
            "modify_user": author,
            "modify_date": func.now(),
        },
    ).returning(*[d.c[k] for k in _WIRE])
    with T.get_engine().begin() as c:
        return dict(c.execute(stmt).mappings().first())


def reorder_dimension(dimension_code: str, item_codes: list[str], *, author: str) -> int:
    """依給定順序重寫某軸的 `sort_order`（拖曳排序用）→ 回更新列數。"""
    if dimension_code not in DIMENSION_CODES:
        raise ValueError(f"值域軸別須為 {' / '.join(DIMENSION_CODES)} 之一")
    d = T.attribution_dimensions
    n = 0
    with T.get_engine().begin() as c:
        for i, code in enumerate(item_codes):
            n += c.execute(
                d.update()
                .where(d.c.dimension_code == dimension_code, d.c.item_code == code)
                .values(sort_order=i, modify_user=author, modify_date=func.now())
            ).rowcount
    return n

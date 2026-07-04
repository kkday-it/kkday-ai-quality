"""上傳批次（batches）+ 來源表批量寫入/讀取（5 來源通用；raw 源欄直存、衝突鍵＝特徵 id）。"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as _pg_insert

from app.core.db import source_registry
from app.core.db import tables as T


def init_db() -> None:
    """建表（冪等）。dev 用 create_all；prod schema 演進交 Alembic。"""
    T.metadata.create_all(T.get_engine())


def create_batch(
    source: str, source_label: str, original_name: str, row_count: int, inserted_count: int
) -> dict:
    """建立上傳批次記錄，自動命名「{來源} YYYYMMDD{當天序號:02d}」。

    例：售前售後進線 2026062301（當天該來源第 1 批）。回傳批次 dict。
    """
    now = datetime.now(timezone.utc).astimezone()
    date_iso = now.strftime("%Y-%m-%d")
    date_compact = now.strftime("%Y%m%d")
    uploaded_at = now.isoformat(timespec="seconds")
    with T.get_engine().begin() as c:
        seq = (
            c.execute(
                select(func.count())
                .select_from(T.batches)
                .where(
                    T.batches.c.source == source,
                    func.substr(T.batches.c.uploaded_at, 1, 10) == date_iso,
                )
            ).scalar()
            + 1
        )
        name = f"{source_label} {date_compact}{seq:02d}"
        batch_id = f"{source}-{date_compact}-{seq:02d}"
        c.execute(
            T.upsert(
                T.batches,
                {
                    "batch_id": batch_id,
                    "name": name,
                    "source": source,
                    "original_name": original_name,
                    "row_count": row_count,
                    "inserted_count": inserted_count,
                    "uploaded_at": uploaded_at,
                },
                ["batch_id"],
            )
        )
    return {
        "batch_id": batch_id,
        "name": name,
        "source": source,
        "original_name": original_name,
        "row_count": row_count,
        "inserted_count": inserted_count,
        "uploaded_at": uploaded_at,
    }


def update_batch_inserted(batch_id: str, inserted_count: int) -> None:
    """回填批次實際落庫筆數（背景上傳 job 逐塊處理完後更新，使批次記錄準確）。"""
    with T.get_engine().begin() as c:
        c.execute(
            T.batches.update().where(T.batches.c.batch_id == batch_id).values(inserted_count=inserted_count)
        )


def list_batches() -> list[dict]:
    """列出上傳批次，新到舊。"""
    stmt = select(T.batches).order_by(T.batches.c.uploaded_at.desc())
    with T.get_engine().connect() as c:
        return [dict(r) for r in c.execute(stmt).mappings()]


def get_items_by_ids(ids: list[str], source: str | None = None) -> list[dict]:
    """依特徵 id（source_id）清單取該來源表列（供 prejudge_batch 批量判決）；空 / 未知來源回 []。

    Args:
        ids: 特徵 id 清單（source_id；product_reviews→rec_oid…）。
        source: 來源 code（必給且須為已登記來源，否則回 []）。

    Returns:
        來源表列 dict 清單（源欄名）；空回 []。
    """
    if not ids:
        return []
    spec = source_registry.spec_for(source)
    if spec is None:
        return []
    stmt = select(spec.table).where(spec.table.c[spec.natural_key].in_(ids))
    with T.get_engine().connect() as c:
        return [dict(r) for r in c.execute(stmt).mappings()]


def insert_source_batch(source: str, rows: list[dict], errors: list[str] | None = None) -> int:
    """批量 upsert 某來源表列（衝突鍵＝該表特徵 id；raw 源欄直存，覆蓋業務欄位）。

    rows 為原始源列 dict（key＝源欄名；mixpanel $ 欄須已淨化為合法名）。分塊 executemany +
    整塊失敗逐列隔離容錯；批內同特徵 id 去重（留最後一筆）。dict/list 值（巢狀 JSON）轉 JSON 字串存 Text。

    Args:
        source: 來源 code（須已登記 source_registry）。
        rows: 源欄 dict 清單。
        errors: 選填；跳過列錯誤原因（最多 10 筆）。

    Returns:
        成功 upsert 筆數；未知來源 / 空 / 全無特徵 id 回 0。
    """
    spec = source_registry.spec_for(source)
    if spec is None or not rows:
        return 0
    tbl = spec.table
    nk = spec.natural_key
    cols = [c.name for c in tbl.columns]
    business_cols = [c for c in cols if c != nk]
    dedup: dict[str, dict] = {}
    for row in rows:
        sid = row.get(nk)
        if sid is None or sid == "":
            continue  # 無特徵 id 者跳過（防禦：避免髒資料以 NULL 衝突鍵批量覆蓋彼此）
        dedup[str(sid)] = row
    clean = list(dedup.values())
    if not clean:
        return 0
    base = _pg_insert(tbl)
    stmt = base.on_conflict_do_update(
        index_elements=[nk], set_={c: base.excluded[c] for c in business_cols}
    )

    def _params(row: dict) -> dict:
        out = {}
        for c in cols:
            v = row.get(c)
            out[c] = json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v
        return out

    eng = T.get_engine()
    inserted = 0
    for i in range(0, len(clean), 1000):  # 分塊 executemany：大檔避免單一巨型 transaction 長鎖
        params = [_params(row) for row in clean[i : i + 1000]]
        try:
            with eng.begin() as c:
                c.execute(stmt, params)
            inserted += len(params)
        except Exception:  # noqa: BLE001 — 整塊失敗 → 逐列隔離跳過壞列，避免單筆髒資料令整批 500
            for p in params:
                try:
                    with eng.begin() as c:
                        c.execute(stmt, [p])
                    inserted += 1
                except Exception as ex:  # noqa: BLE001
                    if errors is not None and len(errors) < 10:
                        errors.append(f"{p.get(nk)}: {type(ex).__name__}: {str(ex)[:160]}")
    return inserted

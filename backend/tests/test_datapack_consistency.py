"""datapack 白名單常數與 `tables.metadata` 的一致性護欄。

**存在的理由**：`.claude/rules/datapack-consistency.md` 列的幾條規則（新表要不要入包、
autoincrement PK 表要進 `_SEQUENCE_TABLES`）目前**完全靠人工遵守**，漏掉不會有任何紅燈：
新表沒進 `TABLE_LOAD_ORDER` 只是匯出時安靜地少一張表；serial PK 表沒進 `_SEQUENCE_TABLES`
要等到「匯入後下一次新增」才以主鍵衝突爆出來，而那時已經離現場很遠了。

本檔把那幾條規則變成執行時檢查。新增/刪除表時本檔若變紅，就是在要求你做一次顯式決定。
"""

from __future__ import annotations

from sqlalchemy import text

from app.core.db import tables as T
from app.core.db.datapack import (
    _LOAD_ORDER_TABLES,
    _SEQUENCE_TABLES,
    SENSITIVE_TABLES,
    TABLE_LOAD_ORDER,
)

# 刻意不入資料包的表 —— 每一張都要有理由，新增表時必須在此二選一（入包或列此）。
_DELIBERATELY_EXCLUDED = {
    # runtime 派生快取（真相源＝production snapshot，可重生），且含 PII-adjacent 商品內容
    "evidence_snapshot_tbl",
}


def test_load_order_tables_all_exist():
    """TABLE_LOAD_ORDER 的每一項都必須是 metadata 裡真實存在的表。"""
    unknown = [t for t in TABLE_LOAD_ORDER if t not in T.metadata.tables]
    assert not unknown, f"TABLE_LOAD_ORDER 含 metadata 不存在的表：{unknown}"


def test_every_table_is_either_packed_or_deliberately_excluded():
    """每張表都必須做過「入不入包」的顯式決定，不能默默漏掉。"""
    packed = set(TABLE_LOAD_ORDER)
    all_tables = set(T.metadata.tables)
    undecided = sorted(all_tables - packed - _DELIBERATELY_EXCLUDED)
    assert not undecided, (
        f"以下表既不在 TABLE_LOAD_ORDER、也不在刻意排除清單：{undecided}。"
        f"請決定：可攜業務資料 → 加進 TABLE_LOAD_ORDER；純衍生/快取 → 加進本測試的 "
        f"_DELIBERATELY_EXCLUDED 並在 datapack.py 註明理由。"
    )
    stale = sorted(_DELIBERATELY_EXCLUDED - all_tables)
    assert not stale, f"刻意排除清單含已不存在的表（表已刪或改名）：{stale}"


def test_serial_pk_tables_are_registered_for_sequence_reset(temp_db):
    """帶序列的 PK 表都要登記於 _SEQUENCE_TABLES，否則匯入後新增會撞主鍵衝突。

    事實源刻意用 **PostgreSQL catalog**（`pg_get_serial_sequence`）而非重算一次 metadata
    判準——舊版本這裡抄了 `_sequence_columns()` 的同一段條件，於是實作因 `Identity()` 會被
    塞進 `server_default` 而漏掉全部 12 張表時，測試也照著漏、一路綠燈。鏡像實作的測試
    驗不了實作本身；要抓錯就得問一個獨立的事實源。
    """
    registered = {(t, c) for t, c in _SEQUENCE_TABLES}
    missing = []
    with T.get_engine().connect() as conn:
        for name in TABLE_LOAD_ORDER:
            for col in T.metadata.tables[name].columns:
                seq = conn.execute(
                    text("SELECT pg_get_serial_sequence(:t, :c)"), {"t": name, "c": col.name}
                ).scalar()
                if seq and (name, col.name) not in registered:
                    missing.append(f"{name}.{col.name}")
    assert not missing, (
        f"以下欄在 DB 有序列卻未登記於 _SEQUENCE_TABLES，匯入還原顯式 id 後續 insert 會主鍵衝突：{missing}"
    )


def test_sequence_entries_point_at_real_columns():
    """_SEQUENCE_TABLES 的 (表, 欄) 都必須真實存在（防派生邏輯或改名造成的幽靈項）。

    ⚠️ 比對的是 **DB 欄名**（`col.name`）而非 `Table.columns` 的鍵——後者是 Python key，
    有 `key=` 別名的欄（如 `attribution_event_oid` 的 key 是 `id`）用 in 判斷會假性失敗。
    `_SEQUENCE_TABLES` 存 DB 欄名是刻意的：它要餵給 `pg_get_serial_sequence()`。
    """
    bad = [
        f"{tname}.{cname}"
        for tname, cname in _SEQUENCE_TABLES
        if tname not in T.metadata.tables
        or cname not in {c.name for c in T.metadata.tables[tname].columns}
    ]
    assert not bad, f"_SEQUENCE_TABLES 指向不存在的表/欄：{bad}"


def test_sensitive_tables_are_packed():
    """敏感表必須也在 TABLE_LOAD_ORDER 內——否則「敏感」的保護語義沒有作用點。"""
    orphan = sorted(SENSITIVE_TABLES - set(TABLE_LOAD_ORDER))
    assert not orphan, f"SENSITIVE_TABLES 含不在 TABLE_LOAD_ORDER 的表：{orphan}"


def test_datapack_uses_column_key_consistently():
    """datapack 的匯出／驗證／匯入三處必須一致以 `col.key`（Python 名）為鍵。

    DDL 規範對齊（2026-08-04）後，34 個欄位的 DB 名與 Python 鍵刻意不同
    （`Column("feedback_source_code", key="source")` 之類）——DB 用規範名、Python 與 wire 維持原名。
    這讓「三處取用不對稱」從理論風險變成**即時會炸的真風險**：匯出端 `dict(row._mapping)` 與驗證端
    `table.columns.keys()` 都是 key，匯入端若改回 `col.name`，資料包會通過驗證卻**整表靜默匯入全 NULL**。

    本測試鎖的是那個不變式本身（而非禁用 key= 別名）：拿真的有別名的表，確認
    「匯出鍵集合 ＝ 驗證鍵集合 ＝ 匯入端查找的鍵集合」。

    ⚠️ `_SEQUENCE_TABLES` 是刻意的例外，用 `col.name`——它餵給 `pg_get_serial_sequence()`，
    那是 DB 層函式、只認 DB 欄名。
    """
    aliased_tables = [t for t in _LOAD_ORDER_TABLES if any(c.name != c.key for c in t.columns)]
    assert aliased_tables, "預期至少一張入包的表有 key= 別名（DDL 對齊後）；若已無別名可刪除本測試"

    for tbl in aliased_tables:
        validate_keys = set(tbl.columns.keys())  # 驗證端
        export_keys = {c.key for c in tbl.columns}  # 匯出端（row._mapping 以 key 為鍵）
        import_keys = {c.key for c in tbl.columns}  # 匯入端 _coerce_row
        assert validate_keys == export_keys == import_keys, (
            f"{tbl.name} 的三處鍵集合不一致——"
            f"驗證 {sorted(validate_keys)} / 匯出 {sorted(export_keys)} / 匯入 {sorted(import_keys)}"
        )

    # 序列重置刻意用 DB 名（pg_get_serial_sequence 只認 DB 欄名）
    db_names = {(t.name, c.name) for t in _LOAD_ORDER_TABLES for c in t.columns}
    for tbl_name, col_name in _SEQUENCE_TABLES:
        assert (tbl_name, col_name) in db_names, (
            f"_SEQUENCE_TABLES 的 {tbl_name}.{col_name} 不是真實 DB 欄名"
        )

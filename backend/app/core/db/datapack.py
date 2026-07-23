"""全庫資料包（datapack）匯入核心 —— 安全還原你當前的全部數據。

設計動機：讓別人 clone 專案後能「一鍵載入你的數據並繼續開發」，且**前台上傳導入不執行任意 SQL**
（避免 RCE / DROP / 機密外洩）。資料包只承載「純資料」（每表一份 ndjson），本模組把它灌進
**程式碼白名單內的固定表**，全程 `table.insert()` 綁定參數，零 SQL 字串拼接。

資料包格式（zip）：
    manifest.json                 # format/schema_version + 每表 row_count/sha256
    tables/<table>.ndjson         # 每行一筆 JSON 物件，key＝該表欄位名

安全要點：
- 表名 / 欄名一律比對 `tables.metadata`（白名單），未知即拒，不觸及 DB。
- schema_version（匯出時 alembic head）與當前 DB head 不符 → 硬拒（無 force），但相等判斷經
  `_heads_compatible()`——一般情況等同直接比對，squash 事件可經 `LEGACY_COMPATIBLE_HEADS`
  登記例外，避免歷史資料包因純粹的 migration 歷史重寫被誤判失效。
- 敏感表（settings，含加密機密）預設不碰；納入需顯式 include_sensitive。
- 匯入為單一交易 truncate-then-load，任一步失敗整體 rollback，DB 維持原狀。

匯出（產生資料包）在 CLI `scripts/tools/dump_datapack.py`，與本模組共用 TABLE_LOAD_ORDER / current_alembic_head。
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from collections.abc import Callable
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, Table, text

from app.core.db import tables as T

if TYPE_CHECKING:
    from app.core.import_jobs import ImportCtx

FORMAT_VERSION = "1.0"

# INSERT 順序（軟關聯：帳號 → 5 來源 → 批次/規則 → 初判 → 依賴/日誌）；TRUNCATE 為反向。
# 本專案零 DB 級 ForeignKey（軟關聯），順序非硬性；仍固定一份 SSOT 供匯入/匯出共用、利審查。
TABLE_LOAD_ORDER: tuple[str, ...] = (
    "settings",
    "product_reviews",
    "conversations",
    "freshdesk_tickets",
    "app_feedback",
    "mixpanel_tracker",
    "batches",
    "judge_rule_versions",
    "prompt_drafts",
    "attributions",
    "finding_notes",
    "llm_usage",
    "prejudge_runs",
    "attribution_history",
)

# 敏感表：含加密機密（LLM token / QC 密碼），預設不匯入（避免跨環境金鑰不符靜默清空）。
SENSITIVE_TABLES: frozenset[str] = frozenset({"settings"})

# 載入後需重置序列的 autoincrement PK 表：還原顯式 id 後，序列未同步會導致後續新增 PK 衝突。
_SEQUENCE_TABLES: tuple[tuple[str, str], ...] = (
    ("judge_rule_versions", "id"),
    ("finding_notes", "id"),
    ("llm_usage", "id"),
    ("attribution_history", "id"),
)

_CHUNK = 2000  # 分塊 insert 大小（對齊 upload_batch 既有分塊量級）

# type-to-confirm 短語（破壞性操作二次確認）：後端定義為 SSOT，validate 回傳給前端顯示，避免兩端 drift。
CONFIRM_PHRASE = "REPLACE-ALL-DATA"


def current_alembic_head(engine: Any | None = None) -> str | None:
    """讀當前 DB 的 alembic 版本（alembic_version.version_num）；表不存在回 None。

    alembic_version 是 Alembic 自管表（非 tables.metadata 一員），故獨立以 text 查詢，不走白名單。

    Args:
        engine: 指定 engine；預設用 tables.get_engine()。

    Returns:
        當前 head revision 字串，或 None（尚未有任何 migration）。
    """
    eng = engine or T.get_engine()
    with eng.connect() as c:
        try:
            row = c.execute(text("SELECT version_num FROM alembic_version LIMIT 1")).first()
        except Exception:  # noqa: BLE001  表不存在 / 尚未 migrate
            return None
    return row[0] if row else None


# 手動維護：squash 事件（歷史 migration 收斂成單一 baseline，schema 內容不變、只重寫歷史）
# 發生時，把「squash 前曾發布過的 head」→「squash 後新 head」登記於此，讓 squash 前匯出的
# 歷史資料包不因純粹的歷史重寫被誤判失效。只在真正的 squash 登記；一般 migration 造成的
# head 變化不該進來——那些是真實 schema 變化，理應讓舊資料包重新驗證（per-table 欄位比對層
# `unknown_cols` 仍會攔截真正不相容的結構差異，見 validate_datapack 迴圈）。
LEGACY_COMPATIBLE_HEADS: dict[str, str] = {
    # 2026-07-23 squash：bd77052f7222 起 53 個增量 migration 併為單一 baseline 4ac23d6d20b4，
    # schema 內容不變，僅 migration 歷史重寫。e2f4a8c91d37 為 squash 前最後一個已提交 head；
    # 另兩個為 squash 當下尚未提交的本機 WIP head，一併登記避免任何時點匯出的資料包被誤判失效。
    "e2f4a8c91d37": "4ac23d6d20b4",
    "b7f4e2a91c56": "4ac23d6d20b4",
    "d3a68f52c910": "4ac23d6d20b4",
}


def _heads_compatible(manifest_head: str | None, current_head: str | None) -> bool:
    """資料包 schema_version 與當前 DB head 是否相容：完全相等，或登記於 LEGACY_COMPATIBLE_HEADS。"""
    if not manifest_head or not current_head:
        return False
    return (
        manifest_head == current_head or LEGACY_COMPATIBLE_HEADS.get(manifest_head) == current_head
    )


def _safe_members(zf: zipfile.ZipFile) -> dict[str, str]:
    """回傳 {table_name: zip內路徑}，只認 `tables/<name>.ndjson`；防 zip-slip / 非預期結構。

    Raises:
        ValueError: 出現路徑穿越（..）或絕對路徑等可疑成員。
    """
    out: dict[str, str] = {}
    for name in zf.namelist():
        if name.endswith("/"):
            continue
        if ".." in name or name.startswith("/"):
            raise ValueError(f"資料包含可疑路徑：{name}")
        if name == "manifest.json":
            continue
        if name.startswith("tables/") and name.endswith(".ndjson"):
            table = name[len("tables/") : -len(".ndjson")]
            out[table] = name
    return out


def _read_manifest(zf: zipfile.ZipFile) -> dict:
    """讀並解析 manifest.json；缺失或格式錯 → ValueError。"""
    try:
        raw = zf.read("manifest.json")
    except KeyError:
        raise ValueError("資料包缺少 manifest.json") from None
    try:
        return json.loads(raw)
    except ValueError as e:
        raise ValueError(f"manifest.json 非合法 JSON：{e}") from None


def _iter_rows(zf: zipfile.ZipFile, member: str):
    """逐行讀 ndjson（streaming，不整檔載入）；空行略過。"""
    with zf.open(member) as fh:
        for line in io.TextIOWrapper(fh, encoding="utf-8"):
            line = line.strip()
            if line:
                yield json.loads(line)


def _first_row_keys(zf: zipfile.ZipFile, member: str) -> set[str]:
    """取某表 ndjson 首筆的 key 集合（供欄位白名單比對）；空表回空集。"""
    for row in _iter_rows(zf, member):
        return set(row.keys())
    return set()


def _db_row_counts() -> dict[str, int]:
    """回傳白名單各表當前列數（供匯入預覽 will_truncate 顯示）。"""
    counts: dict[str, int] = {}
    eng = T.get_engine()
    with eng.connect() as c:
        for name in TABLE_LOAD_ORDER:
            tbl = T.metadata.tables[name]
            counts[name] = c.execute(text(f"SELECT count(*) FROM {tbl.name}")).scalar() or 0  # noqa: S608  name 來自白名單常數
    return counts


def validate_datapack(zip_bytes: bytes, *, include_sensitive: bool = False) -> dict:
    """乾跑校驗資料包（零 DB 寫入）：schema 版本、表名/欄名白名單、每表匯入計畫。

    Args:
        zip_bytes: 上傳的資料包 zip 位元組。
        include_sensitive: 是否納入敏感表（settings）。

    Returns:
        JSON-safe dict：{ok, schema_ok, manifest_head, current_head, tables:[...], errors:[], warnings:[]}。
        端點據 ok 決定 200/400；前端據 tables 畫預覽、schema_ok 畫綠/紅 banner。
    """
    errors: list[str] = []
    warnings: list[str] = []
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile:
        return {"ok": False, "errors": ["上傳檔非合法 zip 資料包"], "warnings": [], "tables": []}

    try:
        members = _safe_members(zf)
        manifest = _read_manifest(zf)
    except ValueError as e:
        return {"ok": False, "errors": [str(e)], "warnings": [], "tables": []}

    manifest_head = manifest.get("schema_version")
    current_head = current_alembic_head()
    schema_ok = _heads_compatible(manifest_head, current_head)
    if not schema_ok:
        errors.append(
            f"資料包 schema_version={manifest_head} 與當前 DB head={current_head} 不符；"
            "請先於此環境跑 alembic upgrade head（或重新匯出資料包）。"
        )

    known = set(T.metadata.tables.keys())
    unknown_tables = sorted(t for t in members if t not in known)
    for t in unknown_tables:
        errors.append(f"資料包含未知表：{t}（不在白名單，拒絕匯入）")

    manifest_tables = manifest.get("tables", {}) or {}
    db_counts = _db_row_counts()
    table_reports: list[dict] = []
    sensitive_present = False

    for name in TABLE_LOAD_ORDER:
        in_pack = name in members
        is_sensitive = name in SENSITIVE_TABLES
        if in_pack and is_sensitive:
            sensitive_present = True
        pack_rows = int((manifest_tables.get(name) or {}).get("row_count", 0)) if in_pack else 0
        unknown_cols: list[str] = []
        if in_pack:
            cols = set(T.metadata.tables[name].columns.keys())
            extra = sorted(_first_row_keys(zf, members[name]) - cols)
            if extra:
                unknown_cols = extra
                errors.append(f"表 {name} 含未知欄位：{'、'.join(extra)}")
        # 匯入計畫：敏感表未納入 → 完全不碰
        touch = in_pack and (include_sensitive or not is_sensitive)
        if in_pack and is_sensitive and not include_sensitive:
            warnings.append(f"敏感表 {name} 存在於資料包但未勾選納入，將完全不碰。")
        table_reports.append(
            {
                "name": name,
                "in_pack": in_pack,
                "sensitive": is_sensitive,
                "pack_rows": pack_rows,
                "db_rows": db_counts.get(name, 0),
                "will_truncate": touch,
                "will_insert": touch,
                "unknown_columns": unknown_cols,
            }
        )

    if sensitive_present:
        warnings.append(
            "資料包含 settings：跨環境還原前確認 AIQ_SECRET_KEY 與來源一致，"
            "否則加密機密（provider token / QC 密碼）匯入後會靜默失效。"
        )

    return {
        "ok": not errors,
        "schema_ok": schema_ok,
        "manifest_head": manifest_head,
        "current_head": current_head,
        "generated_at": manifest.get("generated_at"),
        "sensitive_present": sensitive_present,
        "confirm_phrase": CONFIRM_PHRASE,
        "tables": table_reports,
        "errors": errors,
        "warnings": warnings,
    }


def _coerce_row(table: Table, row: dict) -> dict:
    """把 ndjson 一列轉為可 insert 的值：DateTime(tz) 欄的 ISO 字串 → datetime。

    JSONB / Text / 數值等由 SQLAlchemy Core 綁定處理，無需轉換；只有 DateTime 欄需把字串還原為
    datetime（psycopg2 對 timestamptz 綁定要 datetime 物件）。未知/多餘欄已在 validate 擋下。
    """
    out: dict[str, Any] = {}
    for col in table.columns:
        if col.name not in row:
            continue  # 缺欄＝NULL（由 DB 預設 / nullable 處理）
        val = row[col.name]
        if isinstance(col.type, DateTime) and isinstance(val, str) and val:
            val = datetime.fromisoformat(val)
        out[col.name] = val
    return out


def _reset_sequence(conn: Any, table_name: str, col: str) -> None:
    """把 autoincrement 序列推進到 max(id)+1，避免還原顯式 id 後續新增衝突。"""
    conn.execute(
        text(
            "SELECT setval(pg_get_serial_sequence(:t, :c), "
            "COALESCE((SELECT MAX(" + col + ") FROM " + table_name + "), 0) + 1, false)"  # noqa: S608  t/c 為白名單常數
        ),
        {"t": table_name, "c": col},
    )


def load_datapack(
    zip_bytes: bytes,
    *,
    include_sensitive: bool = False,
    ctx: ImportCtx | None = None,
) -> dict:
    """匯入資料包：單一交易 truncate-then-load 白名單表，任一步失敗整體 rollback。

    先再跑一次 validate（TOCTOU 防護：/validate 與此呼叫間 DB 可能已變），不過即拋 ValueError。

    Args:
        zip_bytes: 資料包 zip 位元組。
        include_sensitive: 是否納入敏感表。
        ctx: 進度回報把手（每表回報）；None 時不回報。

    Returns:
        {inserted: {table: n}, tables: [...]}（實際灌入列數統計）。

    Raises:
        ValueError: 校驗未過（schema 不符 / 未知表欄 / 壞 zip）。
    """
    report = validate_datapack(zip_bytes, include_sensitive=include_sensitive)
    if not report["ok"]:
        raise ValueError("；".join(report["errors"]))

    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    members = _safe_members(zf)

    def _touch(name: str) -> bool:
        return name in members and (include_sensitive or name not in SENSITIVE_TABLES)

    targets = [n for n in TABLE_LOAD_ORDER if _touch(n)]
    inserted: dict[str, int] = {}
    total_tables = len(targets)

    eng = T.get_engine()
    with eng.begin() as conn:
        # 1) 反向 TRUNCATE（僅將匯入的表；敏感表未納入者不動）
        for name in reversed(targets):
            conn.execute(T.metadata.tables[name].delete())
        # 2) 正向逐表分塊 insert
        for idx, name in enumerate(targets):
            table = T.metadata.tables[name]
            n = 0
            batch: list[dict] = []
            for row in _iter_rows(zf, members[name]):
                batch.append(_coerce_row(table, row))
                if len(batch) >= _CHUNK:
                    conn.execute(table.insert(), batch)
                    n += len(batch)
                    batch = []
            if batch:
                conn.execute(table.insert(), batch)
                n += len(batch)
            inserted[name] = n
            if ctx is not None:
                ctx.report_table(name, n, idx + 1, total_tables)
        # 3) 重置 autoincrement 序列（僅已匯入的表）
        for tname, col in _SEQUENCE_TABLES:
            if tname in targets:
                _reset_sequence(conn, tname, col)

    return {"inserted": inserted, "tables": targets}


# ── 匯出（產生資料包）──────────────────────────────────────────────────────
# CLI（scripts/tools/dump_datapack.py）與後端匯出端點（admin_import）共用；勿另造第二套打包邏輯。


def _json_default(o: object) -> str:
    """json.dumps fallback：DateTime(tz) → ISO 字串（匯入端以 fromisoformat 還原）。"""
    if isinstance(o, datetime):
        return o.isoformat()
    raise TypeError(f"不可序列化型別：{type(o).__name__}")


def dump_table_ndjson(conn: Any, name: str) -> tuple[bytes, int]:
    """把一張表 stream 成 ndjson 位元組 + 列數（server-side cursor，避免大表整檔入記憶體）。

    JSONB 欄回傳原生 dict/list（原樣序列化）、DateTime(tz) 欄經 _json_default 轉 ISO 字串。
    """
    tbl = T.metadata.tables[name]
    buf = io.StringIO()
    n = 0
    result = conn.execution_options(stream_results=True).execute(tbl.select())
    for row in result:
        buf.write(json.dumps(dict(row._mapping), ensure_ascii=False, default=_json_default))
        buf.write("\n")
        n += 1
    return buf.getvalue().encode("utf-8"), n


def resolve_export_tables(*, include_sensitive: bool, only: list[str] | None = None) -> list[str]:
    """依 include_sensitive / only 篩出要匯出的表（白名單 ∩ only − 敏感表）。"""
    wanted = set(only) if only else set(TABLE_LOAD_ORDER)
    return [
        t
        for t in TABLE_LOAD_ORDER
        if t in wanted and (include_sensitive or t not in SENSITIVE_TABLES)
    ]


def build_datapack(
    *,
    include_sensitive: bool = False,
    only: list[str] | None = None,
    generated_at: datetime | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> bytes:
    """在記憶體組出資料包 zip 位元組（manifest + 每表 ndjson）；CLI 與匯出端點共用。

    Args:
        include_sensitive: 是否併入敏感表（settings）。
        only: 只匯出指定表（None＝全部白名單）。
        generated_at: manifest 時間戳（預設現在 UTC）。
        progress: 每完成一張表回報 (已完成表數, 總表數)；供背景 job SSE 顯示進度。回呼可拋
            Cancelled 以中止（見 export_jobs.ExportCtx.check）。

    Returns:
        zip 位元組。schema_version＝當前 alembic head（匯入端據此對齊）。
    """
    tables = resolve_export_tables(include_sensitive=include_sensitive, only=only)
    ts = generated_at or datetime.now(timezone.utc)
    total = len(tables)
    manifest: dict[str, Any] = {
        "format_version": FORMAT_VERSION,
        "schema_version": current_alembic_head(),
        "generated_at": ts.isoformat(),
        "generated_by": "app.core.db.datapack.build_datapack",
        "tables": {},
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        with T.get_engine().connect() as conn:
            for i, name in enumerate(tables):
                data, n = dump_table_ndjson(conn, name)
                zf.writestr(f"tables/{name}.ndjson", data)
                manifest["tables"][name] = {
                    "row_count": n,
                    "checksum_sha256": hashlib.sha256(data).hexdigest(),
                }
                if progress:
                    progress(i + 1, total)
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    return buf.getvalue()

"""資料層存取（SQLAlchemy Core · PostgreSQL）— 錄入標的 + 判決結果 + 帳號/設定持久化。

engine 取自 `tables.py`（連線＝`config.env.database_url`，PostgreSQL，對齊 QC DB）。本模組對外
21 個函式簽名 + 回傳形態（list[dict] / dict / bytes / bool / int）穩定，上層 6 處消費者零改。

schema 建立 / 演進：`init_db()` 用 metadata.create_all（dev/測試便利）；prod 用 Alembic 遷移（見 alembic/）。
"""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import cast as sa_cast
from sqlalchemy import func, select
from sqlalchemy import insert as sa_insert
from sqlalchemy import update as sa_update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as _pg_insert
from sqlalchemy.exc import IntegrityError

from app.core import source_registry
from app.core import tables as T
from app.core.paths import AI_JUDGE_DIR as _AI_JUDGE_DIR  # config/ai_judge 目錄（統一定位）
from app.core.paths import (
    GLOBAL_DIR as _GLOBAL_DIR,  # config/global 目錄（product_vertical 等全域配置）
)
from app.core.schema import InboundItem, TicketFinding


class DuplicateEmailError(Exception):
    """email 已存在（create_user 衝突）；上層轉 409。driver-agnostic，不洩漏底層例外型別。"""


def init_db() -> None:
    """建表（冪等）。dev 用 create_all；prod schema 演進交 Alembic。"""
    T.metadata.create_all(T.get_engine())


# ── 錄入標的（intake_items）──────────────────────────────────────────────


def insert_inbound(item: InboundItem) -> None:
    """單筆寫入（冪等：item_id 重複則覆蓋）。"""
    values = {
        "item_id": item.item_id,
        "source": item.source,
        "batch_id": item.batch_id,
        "prod_oid": item.prod_oid,
        "pkg_oid": item.pkg_oid,
        "rating": item.rating,
        "comment": item.comment,
        "raw": json.dumps(item.raw, ensure_ascii=False),
        "status": item.status,
        "created_at": item.created_at,
        "occurred_at": item.occurred_at,
    }
    with T.get_engine().begin() as c:
        c.execute(T.upsert(T.intake_items, values, ["item_id"]))


def insert_inbound_batch(items: list[InboundItem]) -> int:
    """批量寫入，回傳成功筆數（冪等去重後）。"""
    for it in items:
        insert_inbound(it)
    return len(items)


def list_inbound(status: str | None = None, batch_id: str | None = None) -> list[dict]:
    """列出錄入標的（可依 status / batch_id 過濾），新到舊。"""
    stmt = select(T.intake_items)
    if status:
        stmt = stmt.where(T.intake_items.c.status == status)
    if batch_id:
        stmt = stmt.where(T.intake_items.c.batch_id == batch_id)
    stmt = stmt.order_by(T.intake_items.c.created_at.desc())
    with T.get_engine().connect() as c:
        return [dict(r) for r in c.execute(stmt).mappings()]


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


def export_inbound_csv(batch_id: str) -> bytes:
    """把某批次明細匯出為 CSV bytes（utf-8-sig，Excel 友善）。"""
    items = list_inbound(batch_id=batch_id)
    cols = [
        "item_id",
        "source",
        "batch_id",
        "prod_oid",
        "pkg_oid",
        "rating",
        "comment",
        "status",
        "created_at",
    ]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    for it in items:
        w.writerow([it.get(c, "") for c in cols])
    return buf.getvalue().encode("utf-8-sig")


# ── 判決結果（judgments）─────────────────────────────────────────────────


def insert_finding(f: TicketFinding, source: str) -> None:
    """寫入判決結果（冪等：finding_id 重複則覆蓋）。

    Args:
        f: 判決結果。
        source: 標的所屬反饋來源 code（必填——product_reviews 拆表後，下游查詢
            需靠此欄判斷 item_id 該去哪張表 join，不可再由 judgments 缺欄猜測）。
    """
    values = {
        "finding_id": f.finding_id,
        "item_id": f.ticket_id,
        "prod_oid": f.prod_oid,
        "pkg_oid": f.pkg_oid,
        "dimension": f.dimension,
        "confidence": f.confidence,
        "raw_confidence": f.raw_confidence,
        "is_enhanced": int(f.is_enhanced),
        "enhance_model": f.enhance_model,
        "needs_review": int(f.needs_review),
        "suspected_field": f.suspected_field,
        "recommended_action": f.recommended_action,
        "data": f.model_dump_json(),
        "status": f.status,
        "created_at": f.created_at,
        "source": source,
    }
    with T.get_engine().begin() as c:
        c.execute(T.upsert(T.judgments, values, ["finding_id"]))


def insert_findings_batch(items: list[TicketFinding], source: str) -> int:
    """批量寫入判決結果（同 source；見 insert_finding）。"""
    for it in items:
        insert_finding(it, source)
    return len(items)


def list_findings(
    prod_oid: str | None = None,
    dimension: str | None = None,
) -> list[dict]:
    """列出判決結果（可依 prod_oid / dimension 過濾），新到舊。data 還原為完整 Finding。"""
    stmt = select(T.judgments)
    for col, val in (
        (T.judgments.c.prod_oid, prod_oid),
        (T.judgments.c.dimension, dimension),
    ):
        if val:
            stmt = stmt.where(col == val)
    stmt = stmt.order_by(T.judgments.c.created_at.desc())
    out = []
    with T.get_engine().connect() as c:
        for r in c.execute(stmt).mappings():
            d = dict(r)
            if d.get("data"):
                d["finding"] = json.loads(d["data"])
            out.append(d)
    return out


def list_products() -> list[dict]:
    """有 finding 的商品清單（PM 下拉用），依問題數排序。"""
    n = func.count().label("n")
    stmt = (
        select(T.judgments.c.prod_oid, n)
        .where(T.judgments.c.dimension != "non_content")
        .group_by(T.judgments.c.prod_oid)
        .order_by(n.desc())
    )
    with T.get_engine().connect() as c:
        return [dict(r) for r in c.execute(stmt).mappings()]


def update_finding_status(finding_id: str, status: str) -> bool:
    """更新單筆 Finding 狀態（confirmed/dismissed/fixed）。回傳是否命中。"""
    stmt = (
        sa_update(T.judgments).where(T.judgments.c.finding_id == finding_id).values(status=status)
    )
    with T.get_engine().begin() as c:
        return c.execute(stmt).rowcount > 0


def update_inbound_status(item_id: str, status: str) -> bool:
    """更新單筆錄入標的狀態（pending/diagnosed/failed/pending_evidence）。回傳是否命中。"""
    if not item_id:
        return False
    stmt = (
        sa_update(T.intake_items).where(T.intake_items.c.item_id == item_id).values(status=status)
    )
    with T.get_engine().begin() as c:
        return c.execute(stmt).rowcount > 0


def get_inbound_by_ids(item_ids: list[str]) -> list[dict]:
    """依 item_id 清單取錄入標的（判決端點按指定 ids 批量判決用）；空清單回 []。"""
    if not item_ids:
        return []
    stmt = select(T.intake_items).where(T.intake_items.c.item_id.in_(item_ids))
    with T.get_engine().connect() as c:
        return [dict(r) for r in c.execute(stmt).mappings()]


def get_items_by_ids(ids: list[str], source: str | None = None) -> list[dict]:
    """依 item_id 清單取錄入標的，依 source_registry 選表（product_reviews 走專表，否則 fallback intake_items）。

    get_inbound_by_ids 的擴充版：新增 source 參數以支援已拆表來源；未拆表 / None 來源
    行為與 get_inbound_by_ids 完全一致（兩函式並存，prejudge_batch 改呼叫本函式）。

    Args:
        ids: item_id 清單。
        source: 來源 code（None 或未註冊來源 → fallback intake_items 邏輯）。

    Returns:
        錄入標的 dict 清單；空清單回 []。
    """
    if not ids:
        return []
    spec = source_registry.spec_for(source)
    if spec is None:
        return get_inbound_by_ids(ids)
    stmt = select(spec.table).where(spec.table.c.item_id.in_(ids))
    with T.get_engine().connect() as c:
        return [dict(r) for r in c.execute(stmt).mappings()]


# ── product_reviews 專表（拆自 intake_items；見 tables.py 註解）───────────────


def insert_product_reviews_batch(rows: list[dict], errors: list[str] | None = None) -> int:
    """批量 upsert product_reviews 列（衝突鍵 source_record_id，覆蓋業務欄位、保留 xid）。

    PG ON CONFLICT DO UPDATE 只更新 set_ 列出的欄位，xid 不在其中故自動保留原值
    （同一 source_record_id 重複匯入＝更新內容、非新增列）。

    容錯設計（大檔上傳穩定性）：分塊 executemany（效能）；整塊失敗自動 fallback 逐列隔離，
    只跳過真正壞的列、不讓單筆髒資料令整批 rollback + HTTP 500。批內同鍵先去重避免
    ON CONFLICT 同鍵重複影響。

    Args:
        rows: product_reviews_ingest.row_to_product_review 產出的欄位 dict 清單。
        errors: 選填；提供時將跳過列的錯誤原因（最多 10 筆）append 進此清單供上層回報。

    Returns:
        成功 upsert 的唯一 source_record_id 筆數；空清單 / 全無自然鍵回 0。
    """
    if not rows:
        return 0
    pr = T.product_reviews
    business_cols = [c.name for c in pr.columns if c.name not in ("xid", "source_record_id")]
    # 過濾無自然鍵 + 批內去重（同 source_record_id 留最後一筆）：避免 executemany 的 ON CONFLICT
    # 在單一語句內同鍵重複影響（PG 會報 "cannot affect row a second time"）；冪等語義＝後者覆蓋前者。
    dedup: dict[str, dict] = {}
    for row in rows:
        sid = row.get("source_record_id")
        if not sid:
            continue  # 無自然鍵者跳過（防禦：避免髒資料以 NULL 衝突鍵批量覆蓋彼此）
        dedup[sid] = row
    clean = list(dedup.values())
    if not clean:
        return 0
    # executemany 要求每列同鍵：以固定欄位集（除 xid 自增）補齊，缺值填 None。
    cols = [c.name for c in pr.columns if c.name != "xid"]
    base = _pg_insert(pr)
    stmt = base.on_conflict_do_update(
        index_elements=["source_record_id"], set_={c: base.excluded[c] for c in business_cols}
    )
    eng = T.get_engine()
    inserted = 0
    chunk_size = 1000  # 分塊 executemany：29k 列由 60s→數秒，且避免單一巨型 transaction 長鎖/逾時
    for i in range(0, len(clean), chunk_size):
        params = [{c: row.get(c) for c in cols} for row in clean[i : i + chunk_size]]
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
                        errors.append(f"{p.get('source_record_id')}: {type(ex).__name__}: {str(ex)[:160]}")
    return inserted


# ── 帳號系統（users）+ per-user 設定（user_settings）─────────────────────


def create_user(user_id: str, email: str, password_hash: str) -> dict:
    """建立使用者；email 重複拋 DuplicateEmailError（呼叫端轉 409）。回傳 user dict。"""
    created_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    stmt = sa_insert(T.users).values(
        user_id=user_id, email=email, password_hash=password_hash, created_at=created_at
    )
    try:
        with T.get_engine().begin() as c:
            c.execute(stmt)
    except IntegrityError as e:
        raise DuplicateEmailError(email) from e
    return {"user_id": user_id, "email": email, "created_at": created_at}


def get_user_by_email(email: str) -> dict | None:
    """以 email 取使用者（含 password_hash，供登入驗證）；無則 None。"""
    stmt = select(T.users).where(T.users.c.email == email)
    with T.get_engine().connect() as c:
        row = c.execute(stmt).mappings().first()
    return dict(row) if row else None


def get_user_by_id(user_id: str) -> dict | None:
    """以 user_id 取使用者；無則 None。"""
    stmt = select(T.users).where(T.users.c.user_id == user_id)
    with T.get_engine().connect() as c:
        row = c.execute(stmt).mappings().first()
    return dict(row) if row else None


def load_user_settings(user_id: str) -> dict | None:
    """讀某 user 的設定（完整 dict，含明文 token）；尚未存過則回 None（由上層套 _DEFAULT）。"""
    stmt = select(T.user_settings.c.data).where(T.user_settings.c.user_id == user_id)
    with T.get_engine().connect() as c:
        row = c.execute(stmt).first()
    if not row or not row[0]:
        return None
    try:
        return json.loads(row[0])
    except json.JSONDecodeError:
        return None


def save_user_settings(user_id: str, data: dict) -> None:
    """覆寫某 user 的完整設定 dict（冪等：user_id 重複則覆蓋）。"""
    updated_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    values = {
        "user_id": user_id,
        "data": json.dumps(data, ensure_ascii=False),
        "updated_at": updated_at,
    }
    with T.get_engine().begin() as c:
        c.execute(T.upsert(T.user_settings, values, ["user_id"]))


# ── 判決規則版本（config/ai_judge/ 的 7 rule + schema；append-only 快照）────────
# 檔案＝默認 seed（git 版控、不可變）；DB＝live + 完整歷史；一 rule_code 僅一 active。
# _AI_JUDGE_DIR 由 app.core.paths 統一提供（見檔頭 import）。
# 6 歸因域（rebuild 後 force_majeure 併入 customer，無 C-7）+ schema 結構規格 + product_vertical。
# 商品垂直分類（product_vertical：Tour/Exp/Charter/Tix→CATEGORY 代碼）為可編輯版本化規則，
# 復用同一 judge_rule_versions 機制（經 RuleManager 面板編輯/歷史/恢復默認），非歸因分類。
RULE_CODES = ("C-1", "C-2", "C-3", "C-4", "C-5", "C-6", "schema", "product_vertical")


def _rule_file(code: str) -> Path:
    """rule_code → 對應默認檔（schema→rule.schema.json，product_vertical→config/global，C-N→rule_C-N.json）。"""
    if code == "product_vertical":  # 商品垂直分類屬全域配置，默認 seed 放 config/global（非歸因判準）
        return _GLOBAL_DIR / "product_vertical.json"
    return _AI_JUDGE_DIR / ("rule.schema.json" if code == "schema" else f"rule_{code}.json")


def default_rule_content(code: str) -> dict:
    """讀默認檔內容（恢復默認用）；檔不存在拋 FileNotFoundError。"""
    return json.loads(_rule_file(code).read_text(encoding="utf-8"))


def _jrv():  # 縮寫
    return T.judge_rule_versions


def list_rule_meta() -> list[dict]:
    """列所有 rule 的 active 版 meta（rule_code/version/author/note/created_at/label），無 active 者略。

    label 自 content._meta.label（JSONB 路徑抽出，避免拉整份 content）；schema 等無 _meta.label 者回 None，
    由前端 fallback 補顯示名。此為 L1 域中文名的唯一真相源（取代前端 RULE_LABELS 各寫一份而漂移）。
    """
    j = _jrv()
    stmt = (
        select(
            j.c.rule_code,
            j.c.version,
            j.c.author,
            j.c.note,
            j.c.created_at,
            j.c.content["_meta"]["label"].astext.label("label"),
        )
        .where(j.c.is_active.is_(True))
        .order_by(j.c.rule_code)
    )
    with T.get_engine().connect() as c:
        return [dict(r) for r in c.execute(stmt).mappings()]


def get_rule_active(code: str) -> dict | None:
    """取某 rule 的 active 版 content（dict）；無則 None。"""
    j = _jrv()
    stmt = select(j.c.content).where(j.c.rule_code == code, j.c.is_active.is_(True))
    with T.get_engine().connect() as c:
        row = c.execute(stmt).first()
    return row[0] if row else None


def get_rule_version(code: str, version: int) -> dict | None:
    """取某 rule 特定版本的 content（diff/恢復用）；無則 None。"""
    j = _jrv()
    stmt = select(j.c.content).where(j.c.rule_code == code, j.c.version == version)
    with T.get_engine().connect() as c:
        row = c.execute(stmt).first()
    return row[0] if row else None


def list_rule_history(code: str) -> list[dict]:
    """列某 rule 全版本（version/author/note/is_active/created_at），新到舊。"""
    j = _jrv()
    stmt = (
        select(j.c.version, j.c.author, j.c.note, j.c.is_active, j.c.created_at)
        .where(j.c.rule_code == code)
        .order_by(j.c.version.desc())
    )
    with T.get_engine().connect() as c:
        return [dict(r) for r in c.execute(stmt).mappings()]


def save_rule_version(code: str, content: dict, note: str = "", author: str = "") -> dict:
    """存新版本（version=max+1）並切為 active（交易內解除前一 active）。回 {rule_code, version}。"""
    j = _jrv()
    with T.get_engine().begin() as c:
        maxv = c.execute(select(func.max(j.c.version)).where(j.c.rule_code == code)).scalar()
        newv = (maxv or 0) + 1
        c.execute(
            sa_update(j)
            .where(j.c.rule_code == code, j.c.is_active.is_(True))
            .values(is_active=False)
        )
        c.execute(
            sa_insert(j).values(
                rule_code=code,
                version=newv,
                content=content,
                note=note,
                author=author,
                is_active=True,
            )
        )
    return {"rule_code": code, "version": newv}


def restore_rule_version(code: str, version: int, author: str = "") -> dict:
    """恢復某歷史版本（複製其 content 為新 active 版）。回 {rule_code, version}；版本不存在拋 ValueError。"""
    content = get_rule_version(code, version)
    if content is None:
        raise ValueError(f"version {version} not found for {code}")
    return save_rule_version(code, content, note=f"恢復自 v{version}", author=author)


def reset_rule_default(code: str, author: str = "") -> dict:
    """恢復默認（讀 config/ai_judge/ 檔內容存為新 active 版）。回 {rule_code, version}。"""
    return save_rule_version(code, default_rule_content(code), note="恢復默認", author=author)


def reset_all_rule_defaults(author: str = "") -> dict:
    """恢復所有歸因分類（C-N，排除 schema / product_vertical）為檔案默認，各存為新 active 版（覆蓋當前、保留歷史）。

    schema 屬結構規格、product_vertical 屬商品垂直分類，皆非歸因分類，不在此批次。
    缺默認檔的 code 跳過不中斷（如域數調整後殘留、rule_C-*.json 已刪的 code），回報於 skipped。

    Returns:
        {reset: [{rule_code, version}, ...], skipped: [code, ...]}（依 RULE_CODES 順序）。
    """
    done: list[dict] = []
    skipped: list[str] = []
    for code in RULE_CODES:
        if code in ("schema", "product_vertical"):
            continue
        try:
            done.append(reset_rule_default(code, author=author))
        except FileNotFoundError:
            skipped.append(code)  # 該分類無默認檔 → 跳過
    return {"reset": done, "skipped": skipped}


def seed_rules_from_files() -> dict:
    """初次播種：無任何 DB 版的 rule_code 以默認檔建 version 1 active。回各 code 處理結果。"""
    j = _jrv()
    out: dict[str, str] = {}
    with T.get_engine().connect() as c:
        existing = {r[0] for r in c.execute(select(j.c.rule_code).distinct()).all()}
    for code in RULE_CODES:
        if code in existing:
            out[code] = "skip(existed)"
            continue
        try:
            save_rule_version(
                code, default_rule_content(code), note="seed from file", author="system"
            )
            out[code] = "seeded"
        except FileNotFoundError:
            out[code] = "skip(no file)"
    return out


# ── 統一問題列表（intake + 歸因 即時 join；公共欄位於回傳層由 source_mapping 還原）──────


def _extract_prod_name(raw: dict) -> str:
    """從 raw 取商品名：優先 prod_name_zh_tw（進線）；其次 order_snap_json 內各語系 prod_name。"""
    direct = raw.get("prod_name_zh_tw") or raw.get("prod_name")
    if direct:
        return str(direct)
    snap = raw.get("order_snap_json")
    if not snap:
        return ""
    try:
        d = json.loads(snap) if isinstance(snap, str) else snap
    except (ValueError, TypeError):
        return ""
    if not isinstance(d, dict):
        return ""
    for k in ("zh-tw", "zh-hk", "zh-cn", "en"):
        nm = (d.get(k) or {}).get("prod_name")
        if nm:
            return str(nm)
    for v in d.values():  # 任一語系兜底
        if isinstance(v, dict) and v.get("prod_name"):
            return str(v["prod_name"])
    return ""


def _extract_package_name(raw: dict) -> str:
    """從 order_snap_json 多語 dict 取方案名 package_name；語系優先序與 _extract_prod_name 一致。"""
    snap = raw.get("order_snap_json")
    if not snap:
        return ""
    try:
        d = json.loads(snap) if isinstance(snap, str) else snap
    except (ValueError, TypeError):
        return ""
    if not isinstance(d, dict):
        return ""
    for k in ("zh-tw", "zh-hk", "zh-cn", "en"):
        nm = (d.get(k) or {}).get("package_name")
        if nm:
            return str(nm)
    for v in d.values():  # 任一語系兜底
        if isinstance(v, dict) and v.get("package_name"):
            return str(v["package_name"])
    return ""


def _enrich_problem(row: dict, source: str | None = None) -> dict:
    """intake×judgment join 列 → 統一問題列表記錄（公共欄 + 反饋管道 + 歸因）。

    source 命中 source_registry（如 product_reviews）時，row 為該專表欄位（已展開，
    免 source_mapping 還原）；否則 row 為 intake_items 通用欄（raw JSON 經 source_mapping
    即時還原公共欄）。歸因欄（confidence/domain/l3…）兩種形態皆取自 judgments + 其 data JSON，
    邏輯不變。

    Args:
        row: outerjoin 後的 mapping（intake_items 或 product_reviews 全欄 + jg_* 標籤欄）。
        source: 來源 code（None 時退回 row.get("source")，相容 intake_items 路徑既有呼叫）。

    Returns:
        統一記錄 dict（含 source_label / canonical 公共欄 / 歸因欄）。
    """
    from app.core import sources as _sources

    finding: dict = {}
    if row.get("jg_data"):
        try:
            finding = json.loads(row["jg_data"])
        except (ValueError, TypeError):
            finding = {}

    spec = source_registry.spec_for(source)
    if spec is not None:
        # 已拆表來源（product_reviews）：欄位已展開，無需 raw 還原
        base = {
            "item_id": row.get("item_id"),
            "source": source,
            "source_label": _sources.label_for(source),
            "prod_oid": row.get("prod_oid") or "",
            # _extract_prod_name 期待「原始列」形態（含 order_snap_json key）；
            # product_reviews 專表已展開為 prod_name_snapshot 欄，包一層 key 沿用同一解析邏輯。
            "prod_name": _extract_prod_name({"order_snap_json": row.get("prod_name_snapshot")}),
            "pkg_oid": row.get("pkg_oid") or "",
            "content": row.get("content") or "",
            "score": row.get("score"),
            "status": row.get("status"),
            "created_at": row.get("created_at"),
            "order_oid": row.get("order_oid"),
            "order_mid": row.get("order_mid"),
            "go_date": row.get("go_date"),
            "occurred_at": row.get("occurred_at"),
            "title": row.get("title"),
            "channel": "review",
            "lang": row.get("lang"),
            "supplier_oid": row.get("supplier_oid"),
            # 展開行重設計新增：評論ID / 商品分類 / 方案名 / 會員 / 旅客類型（資料已在專表列，直接取）
            "source_record_id": row.get("source_record_id"),  # rec_oid（評論ID）
            "product_category_main": row.get("product_category_main"),
            "package_name": _extract_package_name({"order_snap_json": row.get("prod_name_snapshot")}),
            "member_uuid": row.get("member_uuid"),
            "traveller_type": row.get("traveller_type"),
        }
    else:
        from app.core import source_mapping as _srcmap

        raw: dict = {}
        if row.get("raw"):
            try:
                raw = json.loads(row["raw"])
            except (ValueError, TypeError):
                raw = {}
        src = source or row.get("source") or ""
        canon = _srcmap.normalize_row(src, raw) if src in _srcmap.sources() else {}
        base = {
            "item_id": row.get("item_id"),
            "source": src,
            "source_label": _sources.label_for(src),
            "prod_oid": row.get("prod_oid") or "",
            "prod_name": _extract_prod_name(raw),
            "pkg_oid": row.get("pkg_oid") or "",
            "content": row.get("comment") or canon.get("content") or "",
            "score": row.get("rating"),
            "status": row.get("status"),
            "created_at": row.get("created_at"),
            # 原始關聯欄（raw；訂單/出發日/商品 一併展示導出）
            "order_oid": raw.get("order_oid") or canon.get("order_oid"),
            "order_mid": raw.get("order_mid"),
            "go_date": raw.get("lst_dt_go"),
            # 公共欄（raw 還原）
            "occurred_at": canon.get("occurred_at"),
            "title": canon.get("title"),
            "channel": canon.get("channel"),
            "lang": canon.get("lang"),
            "supplier_oid": canon.get("supplier_oid"),
        }

    base.update(
        {
            # 歸因（judgments；未判決則 None）
            "judged": bool(row.get("jg_finding_id")),
            "confidence": row.get("jg_confidence"),
            "raw_confidence": row.get("jg_raw_confidence"),
            "needs_review": bool(row.get("jg_needs_review")),
            # L1→L3 歸因（取自 judgments.data；歸因列表/概覽展示用）
            "polarity": finding.get("polarity"),
            "l1_domain": finding.get("l1_domain_code"),
            "l1_label": finding.get("l1_label"),
            "l2_code": finding.get("l2_code"),
            "l2_label": finding.get("l2_label"),
            "l3_code": finding.get("l3_code"),
            "l3_label": finding.get("l3_label"),
            "l3_candidates": finding.get("l3_candidates") or [],  # top-3 符合度（透明檢視）
            "confidence_tier": finding.get("confidence_tier"),
            "recommended_action": finding.get("recommended_action"),
            "root_cause_domain": finding.get("root_cause_domain"),
            "sub_cause": finding.get("sub_cause"),
            "dimension": row.get("jg_dimension"),
            "problem_summary": finding.get("problem_summary"),
            "evidence_quote": finding.get("evidence_quote"),
            "reason": finding.get("reason"),
        }
    )
    return base


def list_problems(
    source: str | None = None,
    judged: bool | None = None,
    polarity: str | None = None,
    limit: int = 100,
    offset: int = 0,
    score: list[int] | None = None,
    product_vertical: str | list[str] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    date_field: str = "occurred_at",
    prod_oid: str | None = None,
    order_oid: str | None = None,
    sort_by: str | None = None,
    sort_dir: str = "desc",
) -> dict:
    """統一問題列表（intake/專表 LEFT JOIN judgments），分頁。回 {rows, total}。

    source 命中 source_registry（如 product_reviews）時直接查該專表（表本身已是單一來源，
    免 WHERE source= 過濾）；未命中則沿用 intake_items 舊邏輯。**不做跨表 UNION**——
    這是本次刻意的範圍限制（source=None 時只回 intake_items 全來源，不含已拆表來源）。

    Args:
        source: 來源 code 過濾（product_reviews…）。
        judged: True=僅已歸因 / False=僅未歸因 / None=全部。
        polarity: 傾向過濾（judgments.data.polarity）。
        limit/offset: 分頁。
        score: 星等過濾（IN 清單；僅 source_registry 命中的表可用，intake_items 路徑忽略）。
        product_vertical: 商品垂直分類名（單一或清單；經 product_vertical.codes_for_group 展開為 CATEGORY 代碼）。
        date_from/date_to: 日期區間（'YYYY-MM-DD'，含端點）；比對 date_field 前 10 字。
        date_field: 日期篩選欄名（'occurred_at' | 'go_date'；僅 source_registry 命中的表可用）。

    Returns:
        {"rows": [統一記錄], "total": 符合篩選總數}。
    """
    spec = source_registry.spec_for(source)
    if spec is not None:
        return _list_problems_spec(
            spec, judged, polarity, limit, offset, score, product_vertical, date_from, date_to,
            date_field, prod_oid, order_oid, sort_by, sort_dir,
        )

    ii, jg = T.intake_items, T.judgments
    j = ii.outerjoin(jg, ii.c.item_id == jg.c.item_id)
    sel = select(
        ii,
        jg.c.finding_id.label("jg_finding_id"),
        jg.c.dimension.label("jg_dimension"),
        jg.c.confidence.label("jg_confidence"),
        jg.c.raw_confidence.label("jg_raw_confidence"),
        jg.c.needs_review.label("jg_needs_review"),
        jg.c.data.label("jg_data"),
    ).select_from(j)
    if source:
        sel = sel.where(ii.c.source == source)
    if judged is True:
        sel = sel.where(jg.c.finding_id.isnot(None))
    elif judged is False:
        sel = sel.where(jg.c.finding_id.is_(None))
    if polarity:
        # polarity 存 judgments.data JSON → 以 jsonb 抽出過濾（未判 data 為 null 自然排除）
        sel = sel.where(sa_cast(jg.c.data, JSONB)["polarity"].astext == polarity)
    if prod_oid:
        sel = sel.where(ii.c.prod_oid == prod_oid)
    count_stmt = select(func.count()).select_from(sel.subquery())
    # 純 SQL 穩定排序：預設評論時間 occurred_at DESC（新在前）+ item_id tiebreaker；
    # 全在 SQL 排序，伺服器端分頁（limit/offset）才正確。動態排序走白名單防注入。
    _sort_map = {"occurred_at": ii.c.occurred_at, "confidence": jg.c.confidence}
    sort_col = _sort_map.get(sort_by or "", ii.c.occurred_at)
    ordering = sort_col.asc() if sort_dir == "asc" else sort_col.desc()
    page = sel.order_by(ordering.nullslast(), ii.c.item_id.asc()).limit(limit).offset(offset)
    with T.get_engine().connect() as c:
        total = c.execute(count_stmt).scalar() or 0
        rows = [_enrich_problem(dict(r), source) for r in c.execute(page).mappings()]
    return {"rows": rows, "total": total}


def _vertical_codes(product_vertical: str | list[str] | None) -> list[str]:
    """商品垂直分類分組名 → CATEGORY 代碼清單（多分組 extend 合併；空/None 回空清單）。

    局部 import：product_vertical loader 讀 db.get_rule_active → 頂層 import 會造成循環依賴。
    供 list_problems / overview / breakdown / unjudged 共用（比對 spec.category_col 的 IN 篩選）。
    """
    if not product_vertical:
        return []
    from app.core import product_vertical as _product_vertical

    groups = [product_vertical] if isinstance(product_vertical, str) else list(product_vertical)
    codes: list[str] = []
    for g in groups:
        codes.extend(_product_vertical.codes_for_group(g))
    return codes


def _list_problems_spec(
    spec: source_registry.SourceSpec,
    judged: bool | None,
    polarity: str | None,
    limit: int,
    offset: int,
    score: list[int] | None,
    product_vertical: str | list[str] | None,
    date_from: str | None,
    date_to: str | None,
    date_field: str,
    prod_oid: str | None = None,
    order_oid: str | None = None,
    sort_by: str | None = None,
    sort_dir: str = "desc",
) -> dict:
    """list_problems 的已拆表來源分支（product_reviews 等）：直接查該專表 LEFT JOIN judgments。

    表本身即單一來源，無需 WHERE source= 過濾；score/product_vertical/日期區間為此分支專屬篩選
    （intake_items 通用路徑無對應語意欄，故不共用此函式）。
    """
    tbl = spec.table
    jg = T.judgments
    j = tbl.outerjoin(jg, tbl.c.item_id == jg.c.item_id)
    sel = select(
        tbl,
        jg.c.finding_id.label("jg_finding_id"),
        jg.c.dimension.label("jg_dimension"),
        jg.c.confidence.label("jg_confidence"),
        jg.c.raw_confidence.label("jg_raw_confidence"),
        jg.c.needs_review.label("jg_needs_review"),
        jg.c.data.label("jg_data"),
    ).select_from(j)
    if judged is True:
        sel = sel.where(jg.c.finding_id.isnot(None))
    elif judged is False:
        sel = sel.where(jg.c.finding_id.is_(None))
    if polarity:
        sel = sel.where(sa_cast(jg.c.data, JSONB)["polarity"].astext == polarity)
    if score and spec.score_col:
        sel = sel.where(tbl.c[spec.score_col].in_(score))
    if spec.category_col:
        codes = _vertical_codes(product_vertical)
        if codes:
            sel = sel.where(tbl.c[spec.category_col].in_(codes))
    if prod_oid and "prod_oid" in tbl.c:
        sel = sel.where(tbl.c.prod_oid == prod_oid)
    if order_oid and "order_oid" in tbl.c:
        sel = sel.where(tbl.c.order_oid == order_oid)
    date_col = tbl.c[date_field] if date_field in tbl.c else tbl.c[spec.date_col]
    if date_from:
        sel = sel.where(func.substr(date_col, 1, 10) >= date_from)
    if date_to:
        sel = sel.where(func.substr(date_col, 1, 10) <= date_to)
    count_stmt = select(func.count()).select_from(sel.subquery())
    # 動態排序（白名單防注入；未指定/未知欄一律 occurred_at）；item_id tiebreaker 確保跨頁穩定。
    _sort_map = {
        "occurred_at": tbl.c.occurred_at,
        "go_date": tbl.c.go_date if "go_date" in tbl.c else tbl.c.occurred_at,
        "score": tbl.c[spec.score_col] if spec.score_col else tbl.c.occurred_at,
        "confidence": jg.c.confidence,
    }
    sort_col = _sort_map.get(sort_by or "", tbl.c.occurred_at)
    ordering = sort_col.asc() if sort_dir == "asc" else sort_col.desc()
    page = sel.order_by(ordering.nullslast(), tbl.c.item_id.asc()).limit(limit).offset(offset)
    with T.get_engine().connect() as c:
        total = c.execute(count_stmt).scalar() or 0
        rows = [_enrich_problem(dict(r), spec.source) for r in c.execute(page).mappings()]
    return {"rows": rows, "total": total}


# 導出 CSV 欄位（標題, 記錄鍵）；全繁中、不含 L3 code（code 僅存 DB，不對外顯示）
_EXPORT_COLS: list[tuple[str, str]] = [
    ("item_id", "item_id"),
    ("來源", "source_label"),
    ("商品ID", "prod_oid"),
    ("商品名稱", "prod_name"),
    ("評論", "content"),
    ("星等", "score"),
    ("評論時間", "occurred_at"),
    ("出發日", "go_date"),
    ("訂單", "order_mid"),
    ("傾向", "polarity"),
    ("L1", "l1_label"),
    ("L2", "l2_label"),
    ("L3", "l3_label"),
    ("信心", "confidence"),
    ("原始信心", "raw_confidence"),
    ("分層", "confidence_tier"),
    ("問題摘要", "problem_summary"),
    ("依據", "reason"),
]

# 判決顯示 label + 信心閾值 SSOT＝config/ai_judge/judgment.json（前後端同讀）。
# db 不能 import settings（settings 已 import db → 會循環），故此處以 paths.AI_JUDGE_DIR 自讀該檔。
try:
    _JUDGMENT_CFG: dict = json.loads((_AI_JUDGE_DIR / "judgment.json").read_text(encoding="utf-8"))
except (OSError, ValueError):
    _JUDGMENT_CFG = {}

# 導出/顯示 code → 繁中（DB 仍存 code，僅導出/顯示轉中文）；傾向 4 值取自 judgment.json.polarity_labels
_POLARITY_LABEL_ZH: dict[str, str] = _JUDGMENT_CFG.get("polarity_labels", {})


def fmt_datetime(value, *, date_only: bool = False) -> str:
    """正規化時間字串：去毫秒/去 T·Z；date_only 或時間為 00:00:00 時只留日期。

    來源 raw 時間格式不一（'2026-06-25 07:46:19.810' / ISO 'T...Z'）→ 統一可讀格式，
    導出與前端共用此語義（前端另有同名 JS helper）。非時間字串原樣返回（不誤傷）。
    """
    s = str(value).strip().replace("T", " ")
    if s.endswith("Z"):
        s = s[:-1].strip()
    s = re.sub(r"\.\d+", "", s)  # 去小數秒（.810）
    if date_only or s.endswith(" 00:00:00"):
        return s.split(" ")[0]
    return s


# ── judge 顯示標籤（judgment.json；取代已移除的 taxonomy）─────────────────────────────
# 信心分層 code → 繁中（純顯示）＋ 分桶閾值：改讀 config/ai_judge/judgment.json（SSOT，QC 可免改碼調校）
_TIER_LABEL_ZH = _JUDGMENT_CFG.get("tier_labels", {})
_CONFIDENCE_TIERS = _JUDGMENT_CFG.get(
    "confidence_tiers", {"auto_accept": 0.8, "jury_low": 0.5, "jury_high": 0.7}
)


def _export_cell(key: str, value) -> str:
    """導出單格：時間欄正規化、傾向/分層 code→繁中，其餘原樣（None→空字串）。"""
    if value is None or value == "":
        return ""
    if key == "occurred_at":
        return fmt_datetime(value)
    if key == "go_date":
        return fmt_datetime(value, date_only=True)
    if key == "polarity":
        return _POLARITY_LABEL_ZH.get(value, value)
    if key == "confidence_tier":
        return _TIER_LABEL_ZH.get(value, value)
    return value


def export_problems_csv(
    source: str | None = None,
    polarity: str | None = None,
    judged: bool | None = None,
    item_ids: list[str] | None = None,
    score: list[int] | None = None,
    product_vertical: str | list[str] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> bytes:
    """依篩選/選取導出統一問題列表為 CSV（全量·不受分頁限制；全繁中、無 L3 code）。

    item_ids 給定時只導那些（前端複選/分頁選取）；否則導符合 source/polarity/judged
    + 星等 score / 商品垂直分類 product_vertical / 日期區間的全部——與列表頁篩選一致，
    避免「畫面已篩、導出卻是全量」的不同步。傾向/判決/分層輸出繁中 label（DB 仍存 code）。
    """
    data = list_problems(
        source=source,
        polarity=polarity,
        judged=judged,
        score=score,
        product_vertical=product_vertical,
        date_from=date_from,
        date_to=date_to,
        limit=10_000_000,
    )
    rows = data["rows"]
    if item_ids:
        idset = set(item_ids)
        rows = [r for r in rows if r.get("item_id") in idset]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([c[0] for c in _EXPORT_COLS])
    for r in rows:
        w.writerow([_export_cell(c[1], r.get(c[1], "")) for c in _EXPORT_COLS])
    return ("﻿" + buf.getvalue()).encode("utf-8")  # BOM：Excel 直接開不亂碼


def problems_summary() -> dict:
    """即時匯總（不另存匯總表）：來源分佈 + 歸因域/信心分層 + 總數。

    Returns:
        {total_intake, judged, by_source, by_domain, by_tier}。
    """
    ii, jg = T.intake_items, T.judgments
    cnt = func.count().label("n")
    tiers = _CONFIDENCE_TIERS
    with T.get_engine().connect() as c:
        total_intake = c.execute(select(func.count()).select_from(ii)).scalar() or 0
        judged = c.execute(select(func.count()).select_from(jg)).scalar() or 0
        by_source = [
            dict(r)
            for r in c.execute(
                select(ii.c.source, cnt).group_by(ii.c.source).order_by(cnt.desc())
            ).mappings()
        ]
        # 域 / 信心分層需讀 data JSON / confidence，Python 即時聚合（資料量小）
        by_domain: dict[str, int] = {}
        by_tier = {"auto_accept": 0, "jury": 0, "needs_review": 0}
        for r in c.execute(select(jg.c.confidence, jg.c.data)).mappings():
            conf = r.get("confidence")
            if conf is not None:
                if conf >= tiers["auto_accept"]:
                    by_tier["auto_accept"] += 1
                elif conf >= tiers["jury_low"]:
                    by_tier["jury"] += 1
                else:
                    by_tier["needs_review"] += 1
            if r.get("data"):
                try:
                    dom = json.loads(r["data"]).get("root_cause_domain") or "—"
                except (ValueError, TypeError):
                    dom = "—"
                by_domain[dom] = by_domain.get(dom, 0) + 1
    return {
        "total_intake": total_intake,
        "judged": judged,
        "by_source": by_source,
        "by_domain": [
            {"domain": k, "n": v} for k, v in sorted(by_domain.items(), key=lambda x: -x[1])
        ],
        "by_tier": by_tier,
    }


# ── 信心度校準參數（confidence_calibration；Cleanlab/Platt 離線擬合 → 線上套用）──────


def unjudged_item_ids(
    source: str | None = None, product_vertical: str | list[str] | None = None
) -> list[str]:
    """取未歸因（judgments 無對應 finding）的 item_id 清單（初判歸因 scope=all 標的）。

    只 SELECT item_id、不跑 _enrich_problem，避免對全量 intake（~8 萬列）做 source_mapping
    還原的無謂開銷；供 prejudge_batch 批量派工前一次解析標的集合。source 命中 source_registry
    （product_reviews）時改查該專表，否則 fallback intake_items 舊邏輯。

    Args:
        source: 來源 code 過濾（None＝全部來源；未拆表來源沿用 intake_items）。
        product_vertical: 商品垂直分類分組（全局篩選；僅 spec.category_col 存在的來源生效，
            intake_items fallback 無分類欄故不套——結構性限制）。

    Returns:
        未判 item_id 清單（順序不保證，批量判決不依賴順序）。
    """
    spec = source_registry.spec_for(source)
    if spec is not None:
        tbl = T.judgments
        j = spec.table.outerjoin(tbl, spec.table.c.item_id == tbl.c.item_id)
        stmt = select(spec.table.c.item_id).select_from(j).where(tbl.c.finding_id.is_(None))
        if spec.category_col:
            codes = _vertical_codes(product_vertical)
            if codes:
                stmt = stmt.where(spec.table.c[spec.category_col].in_(codes))
        with T.get_engine().connect() as c:
            return [r[0] for r in c.execute(stmt)]

    ii, jg = T.intake_items, T.judgments
    j = ii.outerjoin(jg, ii.c.item_id == jg.c.item_id)
    stmt = select(ii.c.item_id).select_from(j).where(jg.c.finding_id.is_(None))
    if source:
        stmt = stmt.where(ii.c.source == source)
    with T.get_engine().connect() as c:
        return [r[0] for r in c.execute(stmt)]


# ── 歸因縱覽聚合（縱覽頁專用；problems_summary 的進階版，多 polarity/L1-code/星等/月趨勢）────


def attribution_overview(
    source: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    granularity: str = "month",
    product_vertical: str | list[str] | None = None,
) -> dict:
    """歸因縱覽聚合：一次取齊 KPI + 各維度分布 + 趨勢（避免前端全量 fetch 再算）。

    比 problems_summary 多：傾向(polarity)分布、L1 七域分布、星等分布、月度時序（已判/負向）。
    域軸用 data.l1_domain_code（7-code 機器值），非 problems_summary 的 root_cause_domain 圈號。
    polarity/l1 取自 judgments.data JSON（JSONB 抽出 GROUP BY，與 list_problems 同手法）；
    星等取 intake_items.rating；月份用 occurred_at 前 7 字（YYYY-MM；occurred_at 為 Text，
    免 timezone/格式 cast，最穩）。信心分層走 Python 即時聚合（資料量小，沿用 problems_summary）。

    source 命中 source_registry（product_reviews）時改查該專表（表本身即單一來源，
    不需 WHERE source= 過濾；星等改讀 spec.score_col 而非 intake_items.rating）；
    未命中則行為與改動前完全一致——此函式邏輯複雜，優先保證正確性，僅做選表 + 加 filter
    的最小必要調整（見計畫說明）。

    Args:
        source: 來源 code 過濾（None＝全部來源）。
        date_from: 起日 'YYYY-MM-DD'（含）；比對 occurred_at 前 10 字，None＝不限。
        date_to: 迄日 'YYYY-MM-DD'（含）；None＝不限。
        granularity: 趨勢粒度 year|month|day（預設 month；對應 substr 取 4/7/10 字）。

    Returns:
        {total_intake, judged, attributed, by_polarity, by_l1, by_tier, by_score, trend}。
        attributed＝已判且 data.l1_domain_code 非空（即負向，走過 L1→L3 歸因）。
    """
    spec = source_registry.spec_for(source)
    ii = spec.table if spec is not None else T.intake_items
    score_col = ii.c[spec.score_col] if (spec is not None and spec.score_col) else ii.c.rating
    jg = T.judgments
    cnt = func.count().label("n")
    tiers = _CONFIDENCE_TIERS
    # judgments.data JSON 內的歸因欄（JSONB 抽出，供 GROUP BY / FILTER）
    pol = sa_cast(jg.c.data, JSONB)["polarity"].astext
    l1c = sa_cast(jg.c.data, JSONB)["l1_domain_code"].astext
    l1l = sa_cast(jg.c.data, JSONB)["l1_label"].astext
    j = ii.outerjoin(jg, ii.c.item_id == jg.c.item_id)

    # 趨勢粒度 → occurred_at 前綴長度（年 YYYY / 月 YYYY-MM / 日 YYYY-MM-DD）
    _GRAN_LEN = {"year": 4, "month": 7, "day": 10}
    glen = _GRAN_LEN.get(granularity, 7)

    # 全局商品垂直分類 codes 一次算好（僅 spec.category_col 存在的來源可套）；供 _src 各查詢共用。
    _v_codes = _vertical_codes(product_vertical) if (spec is not None and spec.category_col) else []

    def _src(stmt):
        """套用 source + 日期區間 + 商品垂直分類過濾（None／空＝不限）；日期比對 occurred_at 前 10 字（含端點）。

        spec 命中時該表本身已是單一來源，不再套 WHERE source=（intake_items 才需要此過濾）。
        """
        if source and spec is None:
            stmt = stmt.where(ii.c.source == source)
        if date_from:
            stmt = stmt.where(func.substr(ii.c.occurred_at, 1, 10) >= date_from)
        if date_to:
            stmt = stmt.where(func.substr(ii.c.occurred_at, 1, 10) <= date_to)
        if _v_codes:
            stmt = stmt.where(ii.c[spec.category_col].in_(_v_codes))
        return stmt

    with T.get_engine().connect() as c:
        total_intake = c.execute(_src(select(cnt).select_from(ii))).scalar() or 0
        judged = (
            c.execute(
                _src(select(cnt).select_from(j).where(jg.c.finding_id.isnot(None)))
            ).scalar()
            or 0
        )
        attributed = (
            c.execute(
                _src(select(cnt).select_from(j).where(l1c.isnot(None), l1c != ""))
            ).scalar()
            or 0
        )
        by_polarity_raw = (
            c.execute(
                _src(
                    select(pol.label("k"), cnt)
                    .select_from(j)
                    .where(jg.c.finding_id.isnot(None))
                    .group_by(pol)
                    .order_by(cnt.desc())
                )
            )
            .mappings()
            .all()
        )
        by_l1_raw = (
            c.execute(
                _src(
                    select(l1c.label("code"), l1l.label("label"), cnt)
                    .select_from(j)
                    .where(l1c.isnot(None), l1c != "")
                    .group_by(l1c, l1l)
                    .order_by(cnt.desc())
                )
            )
            .mappings()
            .all()
        )
        # 星等：全量 intake（不限已判），呈現整體品質健康（score_col 依 spec 動態決定，見上方）
        by_score_raw = (
            c.execute(
                _src(
                    select(score_col.label("score"), cnt)
                    .select_from(ii)
                    .where(score_col.isnot(None))
                    .group_by(score_col)
                    .order_by(score_col.asc())
                )
            )
            .mappings()
            .all()
        )
        # 信心分層：Python 即時聚合（與 problems_summary 同閾值口徑）
        by_tier = {"auto_accept": 0, "jury": 0, "needs_review": 0}
        for r in c.execute(
            _src(select(jg.c.confidence).select_from(j).where(jg.c.confidence.isnot(None)))
        ).mappings():
            conf = r["confidence"]
            if conf >= tiers["auto_accept"]:
                by_tier["auto_accept"] += 1
            elif conf >= tiers["jury_low"]:
                by_tier["jury"] += 1
            else:
                by_tier["needs_review"] += 1
        # 趨勢：occurred_at 前 glen 字（依 granularity）→ 已判數 / 負向數
        ym = func.substr(ii.c.occurred_at, 1, glen).label("ym")
        trend_rows = (
            c.execute(
                _src(
                    select(
                        ym,
                        func.count(jg.c.finding_id).label("judged"),
                        func.count().filter(pol == "negative").label("negative"),
                    )
                    .select_from(j)
                    .where(
                        ii.c.occurred_at.isnot(None),
                        ii.c.occurred_at != "",
                        jg.c.finding_id.isnot(None),
                    )
                    .group_by(ym)
                    .order_by(ym.asc())
                )
            )
            .mappings()
            .all()
        )

    by_polarity = [
        {
            "polarity": r["k"] or "unknown",
            "label": _POLARITY_LABEL_ZH.get(r["k"], r["k"] or "未判"),
            "n": r["n"],
        }
        for r in by_polarity_raw
    ]
    by_l1 = [{"code": r["code"], "label": r["label"] or r["code"], "n": r["n"]} for r in by_l1_raw]
    by_score = [{"score": r["score"], "n": r["n"]} for r in by_score_raw]
    trend = {
        "months": [r["ym"] for r in trend_rows],
        "judged": [r["judged"] for r in trend_rows],
        "negative": [r["negative"] for r in trend_rows],
    }
    return {
        "total_intake": total_intake,
        "judged": judged,
        "attributed": attributed,
        "by_polarity": by_polarity,
        "by_l1": by_l1,
        "by_tier": by_tier,
        "by_score": by_score,
        "trend": trend,
    }


def attribution_breakdown(
    source: str | None,
    l1: str,
    date_from: str | None = None,
    date_to: str | None = None,
    product_vertical: str | list[str] | None = None,
) -> dict:
    """某 L1 歸因域下的 L2 / L3 細項分布（縱覽下鑽·懶載）。

    L2/L3 取自 judgments.data JSON（l2_code/l2_label/l3_code/l3_label），限定該 L1 域；
    GROUP BY code（carry label），依筆數降序。空 code 自然排除（非負向無此欄）。

    source 命中 source_registry（product_reviews）時改查該專表（單一來源，免 WHERE source=）；
    未命中則沿用 intake_items 舊邏輯（最小必要調整，同 attribution_overview 說明）。

    Args:
        source: 來源 code 過濾（None＝全部）。
        l1: L1 歸因域 code（如 'supplier'）。

    Returns:
        {l1_code, l1_label, by_l2, by_l3}；by_l2/by_l3 為 [{code, label, n}]。
    """
    spec = source_registry.spec_for(source)
    ii = spec.table if spec is not None else T.intake_items
    jg = T.judgments
    cnt = func.count().label("n")
    d = sa_cast(jg.c.data, JSONB)
    l1c, l1l = d["l1_domain_code"].astext, d["l1_label"].astext
    l2c, l2l = d["l2_code"].astext, d["l2_label"].astext
    l3c, l3l = d["l3_code"].astext, d["l3_label"].astext
    j = ii.outerjoin(jg, ii.c.item_id == jg.c.item_id)
    # 全局商品垂直分類 codes（僅 spec.category_col 存在的來源可套）
    _v_codes = _vertical_codes(product_vertical) if (spec is not None and spec.category_col) else []

    def _level(code_col, label_col):
        """組某層（L2/L3）的 GROUP BY 查詢：限定 L1 域 + 非空 code，依筆數降序。"""
        stmt = (
            select(code_col.label("code"), label_col.label("label"), cnt)
            .select_from(j)
            .where(l1c == l1, code_col.isnot(None), code_col != "")
        )
        if source and spec is None:
            stmt = stmt.where(ii.c.source == source)
        if date_from:
            stmt = stmt.where(func.substr(ii.c.occurred_at, 1, 10) >= date_from)
        if date_to:
            stmt = stmt.where(func.substr(ii.c.occurred_at, 1, 10) <= date_to)
        if _v_codes:
            stmt = stmt.where(ii.c[spec.category_col].in_(_v_codes))
        return stmt.group_by(code_col, label_col).order_by(cnt.desc())

    with T.get_engine().connect() as c:
        l1_label = (
            c.execute(
                select(l1l).select_from(j).where(l1c == l1, l1l.isnot(None)).limit(1)
            ).scalar()
            or l1
        )
        by_l2 = [dict(r) for r in c.execute(_level(l2c, l2l)).mappings()]
        by_l3 = [dict(r) for r in c.execute(_level(l3c, l3l)).mappings()]
    return {"l1_code": l1, "l1_label": l1_label, "by_l2": by_l2, "by_l3": by_l3}

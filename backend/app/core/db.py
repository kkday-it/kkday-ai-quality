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
from sqlalchemy.exc import IntegrityError

from app.core import tables as T
from app.core.schema import ACTIONABLE_VERDICTS, InboundItem, TicketFinding


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


def insert_finding(f: TicketFinding) -> None:
    """寫入判決結果（冪等：finding_id 重複則覆蓋）。"""
    values = {
        "finding_id": f.finding_id,
        "item_id": f.ticket_id,
        "prod_oid": f.prod_oid,
        "pkg_oid": f.pkg_oid,
        "dimension": f.dimension,
        "verdict": f.verdict,
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
    }
    with T.get_engine().begin() as c:
        c.execute(T.upsert(T.judgments, values, ["finding_id"]))


def insert_findings_batch(items: list[TicketFinding]) -> int:
    for it in items:
        insert_finding(it)
    return len(items)


def list_findings(
    prod_oid: str | None = None,
    dimension: str | None = None,
    verdict: str | None = None,
) -> list[dict]:
    """列出判決結果（可依 prod_oid / dimension / verdict 過濾），新到舊。data 還原為完整 Finding。"""
    stmt = select(T.judgments)
    for col, val in (
        (T.judgments.c.prod_oid, prod_oid),
        (T.judgments.c.dimension, dimension),
        (T.judgments.c.verdict, verdict),
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


def aggregate_findings() -> dict:
    """dimension×verdict 熱力矩陣聚合 + KPI（出口B 用）。"""
    cnt = func.count().label("count")
    with T.get_engine().connect() as c:
        matrix = [
            dict(r)
            for r in c.execute(
                select(T.judgments.c.dimension, T.judgments.c.verdict, cnt).group_by(
                    T.judgments.c.dimension, T.judgments.c.verdict
                )
            ).mappings()
        ]
        total = c.execute(select(func.count()).select_from(T.judgments)).scalar() or 0
        # 內容問題 verdict 集合＝schema.ACTIONABLE_VERDICTS（單一真相源；勿在 SQL 硬寫）
        content = (
            c.execute(
                select(func.count())
                .select_from(T.judgments)
                .where(T.judgments.c.verdict.in_(ACTIONABLE_VERDICTS))
            ).scalar()
            or 0
        )
        by_dim = [
            dict(r)
            for r in c.execute(
                select(T.judgments.c.dimension, cnt)
                .where(T.judgments.c.dimension != "non_content")
                .group_by(T.judgments.c.dimension)
                .order_by(cnt.desc())
            ).mappings()
        ]
    return {
        "matrix": matrix,
        "kpi": {
            "total": total,
            "content_issue_pct": round(content / total, 3) if total else 0.0,
            "top_dimension": by_dim[0]["dimension"] if by_dim else "",
        },
        "by_dimension": by_dim,
    }


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
_AI_JUDGE_DIR = Path(__file__).resolve().parents[3] / "config" / "ai_judge"
RULE_CODES = ("C-1", "C-2", "C-3", "C-4", "C-5", "C-6", "C-7", "schema")


def _rule_file(code: str) -> Path:
    """rule_code → 對應默認檔（schema→schema.json，C-N→rule_C-N.json）。"""
    return _AI_JUDGE_DIR / ("schema.json" if code == "schema" else f"rule_{code}.json")


def default_rule_content(code: str) -> dict:
    """讀默認檔內容（恢復默認用）；檔不存在拋 FileNotFoundError。"""
    return json.loads(_rule_file(code).read_text(encoding="utf-8"))


def _jrv():  # 縮寫
    return T.judge_rule_versions


def list_rule_meta() -> list[dict]:
    """列所有 rule 的 active 版 meta（rule_code/version/author/note/created_at），無 active 者略。"""
    j = _jrv()
    stmt = (
        select(j.c.rule_code, j.c.version, j.c.author, j.c.note, j.c.created_at)
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


def _enrich_problem(row: dict) -> dict:
    """intake×judgment join 列 → 統一問題列表記錄（公共欄 + 反饋管道 + 歸因）。

    公共欄（occurred_at/title/channel/lang/order_oid/supplier_oid）由 raw 經 source_mapping 即時還原，
    免欄位 migration；歸因欄（verdict/confidence/domain/l3…）取自 judgments + 其 data JSON。

    Args:
        row: outerjoin 後的 mapping（intake 全欄 + jg_* 標籤欄）。

    Returns:
        統一記錄 dict（含 source_label / canonical 公共欄 / 歸因欄）。
    """
    from app.core import source_mapping as _srcmap

    raw: dict = {}
    if row.get("raw"):
        try:
            raw = json.loads(row["raw"])
        except (ValueError, TypeError):
            raw = {}
    source = row.get("source") or ""
    canon = _srcmap.normalize_row(source, raw) if source in _srcmap.sources() else {}
    finding: dict = {}
    if row.get("jg_data"):
        try:
            finding = json.loads(row["jg_data"])
        except (ValueError, TypeError):
            finding = {}
    return {
        "item_id": row.get("item_id"),
        "source": source,
        "source_label": _srcmap.source_label(source),
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
        # 歸因（judgments；未判決則 None）
        "judged": bool(row.get("jg_finding_id")),
        "verdict": row.get("jg_verdict"),
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


def list_problems(
    source: str | None = None,
    verdict: str | None = None,
    judged: bool | None = None,
    polarity: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """統一問題列表（intake LEFT JOIN judgments），分頁。回 {rows, total}。

    Args:
        source: 來源 code 過濾（product_reviews…）。
        verdict: 判決過濾。
        judged: True=僅已歸因 / False=僅未歸因 / None=全部。
        limit/offset: 分頁。

    Returns:
        {"rows": [統一記錄], "total": 符合篩選總數}。
    """
    ii, jg = T.intake_items, T.judgments
    j = ii.outerjoin(jg, ii.c.item_id == jg.c.item_id)
    sel = select(
        ii,
        jg.c.finding_id.label("jg_finding_id"),
        jg.c.verdict.label("jg_verdict"),
        jg.c.dimension.label("jg_dimension"),
        jg.c.confidence.label("jg_confidence"),
        jg.c.raw_confidence.label("jg_raw_confidence"),
        jg.c.needs_review.label("jg_needs_review"),
        jg.c.data.label("jg_data"),
    ).select_from(j)
    if source:
        sel = sel.where(ii.c.source == source)
    if verdict:
        sel = sel.where(jg.c.verdict == verdict)
    if judged is True:
        sel = sel.where(jg.c.finding_id.isnot(None))
    elif judged is False:
        sel = sel.where(jg.c.finding_id.is_(None))
    if polarity:
        # polarity 存 judgments.data JSON → 以 jsonb 抽出過濾（未判 data 為 null 自然排除）
        sel = sel.where(sa_cast(jg.c.data, JSONB)["polarity"].astext == polarity)
    count_stmt = select(func.count()).select_from(sel.subquery())
    # 純 SQL 穩定排序：評論時間 occurred_at DESC（新在前）+ item_id tiebreaker。
    # 全在 SQL 排序，伺服器端分頁（limit/offset）才正確；occurred_at 為欄位故可索引。
    page = (
        sel.order_by(ii.c.occurred_at.desc().nullslast(), ii.c.item_id.asc())
        .limit(limit)
        .offset(offset)
    )
    with T.get_engine().connect() as c:
        total = c.execute(count_stmt).scalar() or 0
        rows = [_enrich_problem(dict(r)) for r in c.execute(page).mappings()]
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
    ("判決", "verdict"),
    ("信心", "confidence"),
    ("原始信心", "raw_confidence"),
    ("分層", "confidence_tier"),
    ("問題摘要", "problem_summary"),
    ("依據", "reason"),
]

# 導出時 code → 繁中的欄位（值為 SSOT label map 來源）；DB 仍存 code，僅導出/顯示轉中文
# 傾向 4 值 SSOT（與前端 AttributionList POLARITY_LABEL 對齊）
_POLARITY_LABEL_ZH: dict[str, str] = {
    "positive": "正向",
    "negative": "負向",
    "neutral": "中性",
    "unknown": "數據不足",
}


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


# ── judge 顯示標籤（verdicts.json + inline 分層常數；取代已移除的 taxonomy）──────────────
_AI_JUDGE_DIR = Path(__file__).resolve().parents[3] / "config" / "ai_judge"
# 信心分層 code → 繁中（純顯示）＋ 分桶閾值（不再耦合已移除的 confidence.json）
_TIER_LABEL_ZH = {"auto_accept": "自動採信", "jury": "jury 覆核", "needs_review": "待人工", "hold": "HOLD"}
_CONFIDENCE_TIERS = {"auto_accept": 0.8, "jury_low": 0.5, "jury_high": 0.7}


def _verdict_labels() -> dict[str, str]:
    """verdict code → 繁中 label（讀 config/ai_judge/verdicts.json；失敗回空 map）。"""
    try:
        items = json.loads((_AI_JUDGE_DIR / "verdicts.json").read_text(encoding="utf-8"))["items"]
        return {v["code"]: v.get("label_zh", v["code"]) for v in items}
    except (OSError, ValueError, KeyError):
        return {}


def _export_cell(key: str, value) -> str:
    """導出單格：時間欄正規化、傾向/判決/分層 code→繁中，其餘原樣（None→空字串）。"""
    if value is None or value == "":
        return ""
    if key == "occurred_at":
        return fmt_datetime(value)
    if key == "go_date":
        return fmt_datetime(value, date_only=True)
    if key == "polarity":
        return _POLARITY_LABEL_ZH.get(value, value)
    if key == "verdict":
        return _verdict_labels().get(value, value)
    if key == "confidence_tier":
        return _TIER_LABEL_ZH.get(value, value)
    return value


def export_problems_csv(
    source: str | None = None,
    polarity: str | None = None,
    judged: bool | None = None,
    item_ids: list[str] | None = None,
) -> bytes:
    """依篩選/選取導出統一問題列表為 CSV（全量·不受分頁限制；全繁中、無 L3 code）。

    item_ids 給定時只導那些（前端複選/分頁選取）；否則導符合 source/polarity/judged 的全部。
    傾向/判決/分層輸出繁中 label（DB 仍存 code，見 _export_cell）。
    """
    data = list_problems(source=source, polarity=polarity, judged=judged, limit=10_000_000)
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
    """即時匯總（不另存匯總表）：來源分佈 + 歸因 verdict/域/信心分層 + 總數。

    Returns:
        {total_intake, judged, by_source, by_verdict, by_domain, by_tier}。
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
        by_verdict = [
            dict(r)
            for r in c.execute(
                select(jg.c.verdict, cnt).group_by(jg.c.verdict).order_by(cnt.desc())
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
        "by_verdict": by_verdict,
        "by_domain": [
            {"domain": k, "n": v} for k, v in sorted(by_domain.items(), key=lambda x: -x[1])
        ],
        "by_tier": by_tier,
    }


# ── 信心度校準參數（confidence_calibration；Cleanlab/Platt 離線擬合 → 線上套用）──────


def get_calibration(scope: str, model: str) -> dict | None:
    """取某 (scope, model) 的 Platt 校準參數 {intercept, slope}；無則 None（線上 identity）。

    Args:
        scope: 'global' 或 'domain:<code>' 粒度。
        model: 生效 LLM 模型名（不同模型校準曲線不同）。

    Returns:
        {"intercept", "slope"} 或 None。
    """
    cc = T.confidence_calibration
    stmt = select(cc.c.intercept, cc.c.slope).where(cc.c.scope == scope, cc.c.model == model)
    with T.get_engine().connect() as c:
        r = c.execute(stmt).mappings().first()
        return dict(r) if r else None


def upsert_calibration(scope: str, model: str, intercept: float, slope: float) -> None:
    """寫入/更新校準參數（離線擬合後呼叫）。"""
    values = {
        "scope": scope,
        "model": model,
        "intercept": intercept,
        "slope": slope,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    with T.get_engine().begin() as c:
        c.execute(T.upsert(T.confidence_calibration, values, ["scope", "model"]))


def calibration_training_data(model: str | None = None) -> list[tuple[float, int]]:
    """擷取離線校準訓練樣本：(raw_confidence, correct) — correct＝預測 l3_code 是否＝true_label。

    僅取已人工標註（true_label 非空）且有 raw_confidence 的 judgments。

    Args:
        model: 限定模型（None＝全部）。

    Returns:
        [(raw_confidence, 1|0)]；correct 以 data.l3_code == true_label 判定。
    """
    jg = T.judgments
    stmt = select(jg.c.raw_confidence, jg.c.true_label, jg.c.data).where(
        jg.c.true_label.isnot(None), jg.c.raw_confidence.isnot(None)
    )
    out: list[tuple[float, int]] = []
    with T.get_engine().connect() as c:
        for r in c.execute(stmt).mappings():
            try:
                pred = json.loads(r["data"] or "{}").get("l3_code") or ""
            except (ValueError, TypeError):
                pred = ""
            if model:
                try:
                    if json.loads(r["data"] or "{}").get("model_used") != model:
                        continue
                except (ValueError, TypeError):
                    continue
            out.append((float(r["raw_confidence"]), 1 if pred == r["true_label"] else 0))
    return out


# ── 歸因縱覽聚合（縱覽頁專用；problems_summary 的進階版，多 polarity/L1-code/星等/月趨勢）────


def attribution_overview(source: str | None = None) -> dict:
    """歸因縱覽聚合：一次取齊 KPI + 各維度分布 + 月趨勢（避免前端全量 fetch 再算）。

    比 problems_summary 多：傾向(polarity)分布、L1 七域分布、星等分布、月度時序（已判/負向）。
    域軸用 data.l1_domain_code（7-code 機器值），非 problems_summary 的 root_cause_domain 圈號。
    polarity/l1 取自 judgments.data JSON（JSONB 抽出 GROUP BY，與 list_problems 同手法）；
    星等取 intake_items.rating；月份用 occurred_at 前 7 字（YYYY-MM；occurred_at 為 Text，
    免 timezone/格式 cast，最穩）。信心分層走 Python 即時聚合（資料量小，沿用 problems_summary）。

    Args:
        source: 來源 code 過濾（None＝全部來源）。

    Returns:
        {total_intake, judged, attributed, by_polarity, by_l1, by_verdict, by_tier, by_score, trend}。
        attributed＝已判且 data.l1_domain_code 非空（即負向，走過 L1→L3 歸因）。
    """
    ii, jg = T.intake_items, T.judgments
    cnt = func.count().label("n")
    tiers = _CONFIDENCE_TIERS
    vd_label = _verdict_labels()
    # judgments.data JSON 內的歸因欄（JSONB 抽出，供 GROUP BY / FILTER）
    pol = sa_cast(jg.c.data, JSONB)["polarity"].astext
    l1c = sa_cast(jg.c.data, JSONB)["l1_domain_code"].astext
    l1l = sa_cast(jg.c.data, JSONB)["l1_label"].astext
    j = ii.outerjoin(jg, ii.c.item_id == jg.c.item_id)

    def _src(stmt):
        """套用 source 過濾（None＝不限）。"""
        return stmt.where(ii.c.source == source) if source else stmt

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
        by_verdict_raw = (
            c.execute(
                _src(
                    select(jg.c.verdict.label("verdict"), cnt)
                    .select_from(j)
                    .where(jg.c.verdict.isnot(None))
                    .group_by(jg.c.verdict)
                    .order_by(cnt.desc())
                )
            )
            .mappings()
            .all()
        )
        # 星等：全量 intake（不限已判），呈現整體品質健康
        by_score_raw = (
            c.execute(
                _src(
                    select(ii.c.rating.label("score"), cnt)
                    .select_from(ii)
                    .where(ii.c.rating.isnot(None))
                    .group_by(ii.c.rating)
                    .order_by(ii.c.rating.asc())
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
        # 月趨勢：YYYY-MM（occurred_at 前 7 字）→ 已判數 / 負向數
        ym = func.substr(ii.c.occurred_at, 1, 7).label("ym")
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
    by_verdict = [
        {"verdict": r["verdict"], "label": vd_label.get(r["verdict"], r["verdict"]), "n": r["n"]}
        for r in by_verdict_raw
    ]
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
        "by_verdict": by_verdict,
        "by_tier": by_tier,
        "by_score": by_score,
        "trend": trend,
    }


def attribution_breakdown(source: str | None, l1: str) -> dict:
    """某 L1 歸因域下的 L2 / L3 細項分布（縱覽下鑽·懶載）。

    L2/L3 取自 judgments.data JSON（l2_code/l2_label/l3_code/l3_label），限定該 L1 域；
    GROUP BY code（carry label），依筆數降序。空 code 自然排除（非負向無此欄）。

    Args:
        source: 來源 code 過濾（None＝全部）。
        l1: L1 歸因域 code（如 'supplier'）。

    Returns:
        {l1_code, l1_label, by_l2, by_l3}；by_l2/by_l3 為 [{code, label, n}]。
    """
    ii, jg = T.intake_items, T.judgments
    cnt = func.count().label("n")
    d = sa_cast(jg.c.data, JSONB)
    l1c, l1l = d["l1_domain_code"].astext, d["l1_label"].astext
    l2c, l2l = d["l2_code"].astext, d["l2_label"].astext
    l3c, l3l = d["l3_code"].astext, d["l3_label"].astext
    j = ii.outerjoin(jg, ii.c.item_id == jg.c.item_id)

    def _level(code_col, label_col):
        """組某層（L2/L3）的 GROUP BY 查詢：限定 L1 域 + 非空 code，依筆數降序。"""
        stmt = (
            select(code_col.label("code"), label_col.label("label"), cnt)
            .select_from(j)
            .where(l1c == l1, code_col.isnot(None), code_col != "")
        )
        if source:
            stmt = stmt.where(ii.c.source == source)
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

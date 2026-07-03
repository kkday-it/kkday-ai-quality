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

from sqlalchemy import and_, exists, func, select
from sqlalchemy import cast as sa_cast
from sqlalchemy import delete as sa_delete
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
from app.core.schema import TicketFinding


class DuplicateEmailError(Exception):
    """email 已存在（create_user 衝突）；上層轉 409。driver-agnostic，不洩漏底層例外型別。"""


def init_db() -> None:
    """建表（冪等）。dev 用 create_all；prod schema 演進交 Alembic。"""
    T.metadata.create_all(T.get_engine())


# ── 上傳批次（batches）─────────────────────────────────────────────────────


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


# ── 判決結果（judgments）─────────────────────────────────────────────────


def _finding_values(f: TicketFinding, source: str) -> dict:
    """TicketFinding → judgments 欄位 dict（source + source_id 關聯鍵；source_id 存於 f.ticket_id）。"""
    return {
        "finding_id": f.finding_id,
        "source": source,
        "source_id": f.ticket_id,  # prejudge 設 ticket_id = 特徵 id（source_id）
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
    }


def insert_finding(f: TicketFinding, source: str) -> None:
    """寫入判決結果（冪等：finding_id 重複則覆蓋）。source 定表、f.ticket_id 為特徵 id（source_id）。"""
    with T.get_engine().begin() as c:
        c.execute(T.upsert(T.judgments, _finding_values(f, source), ["finding_id"]))


def insert_findings_batch(items: list[TicketFinding], source: str) -> int:
    """批量寫入判決結果（同 source；見 insert_finding）。"""
    for it in items:
        insert_finding(it, source)
    return len(items)


def replace_source_findings(source: str, source_id: str, findings: list[TicketFinding]) -> int:
    """整組替換某來源列的所有歸因（1:N；刪 (source, source_id) 舊列 → 插新列），保留人工 true_label。

    多歸因下一個來源列對應多筆 judgments（每域一筆）；重判以最新結果整組替換舊列（冪等），非逐筆
    upsert——否則舊域殘留孤兒列。刪除前撈各列 true_label 依 finding_id 回填（同域重判 finding_id 不變＝標註保留）。
    判決引擎（prejudge_batch._work_one）全 5 來源統一走此。

    Args:
        source: 來源 code。
        source_id: 該來源列特徵 id（product_reviews→rec_oid…）。
        findings: 判決結果清單（to_findings 產出，≥1 筆）。

    Returns:
        寫入的歸因列數。
    """
    if not source_id:
        return 0
    jg = T.judgments
    key = and_(jg.c.source == source, jg.c.source_id == source_id)
    with T.get_engine().begin() as c:
        preserved = {
            r.finding_id: r.true_label
            for r in c.execute(
                select(jg.c.finding_id, jg.c.true_label).where(key, jg.c.true_label.isnot(None))
            )
        }
        c.execute(sa_delete(jg).where(key))
        for f in findings:
            values = _finding_values(f, source)
            if f.finding_id in preserved:
                values["true_label"] = preserved[f.finding_id]
            c.execute(sa_insert(jg).values(**values))
    return len(findings)


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


# ── 來源表批量寫入（5 來源通用；raw 源欄直存、衝突鍵＝特徵 id）─────────────────


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
RULE_CODES = ("C-1", "C-2", "C-3", "C-4", "C-5", "C-6", "schema", "product_vertical", "global_rule")


def _rule_file(code: str) -> Path:
    """rule_code → 對應默認檔（schema→rule.schema.json，product_vertical→config/global，global_rule→config/ai_judge，C-N→rule_C-N.json）。"""
    if code == "product_vertical":  # 商品垂直分類屬全域配置，默認 seed 放 config/global（非歸因判準）
        return _GLOBAL_DIR / "product_vertical.json"
    if code == "global_rule":  # 整體規則（判決流程總規範）與判決 config 同置，默認 seed 放 config/ai_judge
        return _AI_JUDGE_DIR / "global_rule.json"
    return _AI_JUDGE_DIR / ("rule.schema.json" if code == "schema" else f"rule_{code}.json")


def default_rule_content(code: str) -> dict:
    """讀默認檔內容（恢復默認用）；檔不存在拋 FileNotFoundError。"""
    return json.loads(_rule_file(code).read_text(encoding="utf-8"))


def _jrv():  # 縮寫
    return T.judge_rule_versions


def list_rule_meta() -> list[dict]:
    """列所有 rule 的 active 版 meta（rule_code/version/author/note/created_at/label），無 active 者略。

    label 優先取 `tree[0].label`（＝L1 域節點名，也是 AI 判決 l1_label 與歸因列表顯示名），使左側菜單
    與樹/判決/歸因列表**單一真相源、不漂移**；無 tree 的 rule（schema/global_rule/product_vertical）
    回退 `_meta.label`，再無則 None 由前端 fallback 補（JSONB 路徑抽出，避免拉整份 content）。
    """
    j = _jrv()
    stmt = (
        select(
            j.c.rule_code,
            j.c.version,
            j.c.author,
            j.c.note,
            j.c.created_at,
            func.coalesce(
                j.c.content["tree"][0]["label"].astext,
                j.c.content["_meta"]["label"].astext,
            ).label("label"),
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
    """恢復規則配置頁所有規則（schema + global_rule + C-N）為檔案默認，各存為新 active 版（覆蓋當前、保留歷史）。

    範圍＝規則配置頁顯示的全部（schema 結構規格 + global_rule 整體規則 + C-N 歸因分類）；
    product_vertical（商品垂直分類）於設定抽屜獨立管理、不在規則配置頁，故排除。
    缺默認檔的 code 跳過不中斷（如域數調整後殘留、rule_C-*.json 已刪的 code），回報於 skipped。

    Returns:
        {reset: [{rule_code, version}, ...], skipped: [code, ...]}（依 RULE_CODES 順序）。
    """
    done: list[dict] = []
    skipped: list[str] = []
    for code in RULE_CODES:
        if code == "product_vertical":  # 商品垂直分類獨立於設定抽屜，不在規則配置頁 reset-all 範圍
            continue
        try:
            done.append(reset_rule_default(code, author=author))
        except FileNotFoundError:
            skipped.append(code)  # 該 rule 無默認檔 → 跳過
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


def _parse_category_main(value) -> str | None:
    """product_category（源欄，raw `{"main":..,"sub":[]}` JSON / list / 純代碼）→ main 代碼。"""
    if not value:
        return None
    v = value
    if isinstance(v, str):
        s = v.strip()
        try:
            v = json.loads(s)
        except (ValueError, TypeError):
            return s or None  # 純代碼字串（如 CATEGORY_082）
    if isinstance(v, dict):
        return v.get("main") or None
    if isinstance(v, list):
        return (str(v[0]) if v else None)
    return str(v) if v else None


def _enrich_problem(row: dict, source: str | None = None) -> dict:
    """來源表列 × judgment join 列 → 統一問題列表記錄（canonical 顯示欄 + 歸因）。

    5 來源統一：row 為該來源表列（源欄名）+ jg_* 標籤欄；canonical 顯示欄一律經
    source_mapping.normalize_row(source, row)（源欄→canonical）產出，不再分「已拆表 vs intake」兩路。
    `source_id`＝該表特徵 id（spec.natural_key 欄值）；`item_id` 為傳輸/顯示相容字串 `{source}:{source_id}`。

    Args:
        row: 來源表列（源欄）+ jg_finding_id/jg_confidence/jg_data… 標籤欄。
        source: 來源 code（None 時退回 row.get("source")）。

    Returns:
        統一記錄 dict（source_id / canonical 公共欄 / 歸因欄）。
    """
    from app.core import source_mapping as _srcmap
    from app.core import sources as _sources

    finding: dict = {}
    if row.get("jg_data"):
        try:
            finding = json.loads(row["jg_data"])
        except (ValueError, TypeError):
            finding = {}

    src = source or row.get("source") or ""
    spec = source_registry.spec_for(src)
    canon = _srcmap.normalize_row(src, row) if src in _srcmap.sources() else {}
    source_id = row.get(spec.natural_key) if spec else canon.get("source_record_id")
    # 商品名：product_reviews.order_snap_json（多語快照 JSON）/ conversations.prod_name_zh_tw
    snap = row.get("order_snap_json")
    base = {
        "source_id": source_id,
        # 傳輸/顯示相容鍵（前端 rowKey 退回 / 導出 / selectedKeys 用單一字串；派生自 source_id）
        "item_id": f"{src}:{source_id}" if source_id is not None else None,
        "source": src,
        "source_label": _sources.label_for(src),
        "prod_oid": canon.get("prod_oid") or "",
        "prod_name": _extract_prod_name({"order_snap_json": snap}) if snap else (row.get("prod_name_zh_tw") or ""),
        "package_name": _extract_package_name({"order_snap_json": snap}) if snap else "",
        "pkg_oid": canon.get("pkg_oid") or "",
        "content": canon.get("content") or "",
        "score": canon.get("score"),
        "occurred_at": canon.get("occurred_at"),
        "title": canon.get("title"),
        "channel": canon.get("channel"),
        "lang": canon.get("lang"),
        "order_oid": canon.get("order_oid"),
        "order_mid": row.get("order_mid"),  # 同名源欄（pr/conv/mixpanel 有；freshdesk/appf 無→None）
        "supplier_oid": canon.get("supplier_oid"),
        "go_date": canon.get("go_date"),
        "member_uuid": canon.get("member_uuid"),
        "traveller_type": canon.get("traveller_type"),
        "product_category_main": _parse_category_main(canon.get("product_category")),
        "source_record_id": source_id,  # 評論ID（＝特徵 id）
        "status": None,
        "created_at": None,
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
            "judgment_stage": _stage_of(row, finding),
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


def _stage_of(row: dict, finding: dict) -> str:
    """判決階段顯示值：無 finding→未判(unjudged)；有存 judgment_stage 直接用；
    舊資料(prejudge 加欄前)無此欄則即時派生（不含 evidence_capped，供列表相容顯示）。"""
    if not row.get("jg_finding_id"):
        return "unjudged"
    st = finding.get("judgment_stage")
    if st:
        return st
    pol = finding.get("polarity")
    if pol == "unknown":
        return "insufficient"
    if pol != "negative":
        return "judged"
    if not finding.get("l3_code"):
        return "pending_data"
    return "judged" if finding.get("confidence_tier") == "auto_accept" else "pending_review"


def _attribution_of(r: dict) -> dict:
    """單筆 judgments join 列 → 一條歸因顯示 dict（供列表右側堆疊 / 導出 fan-out；欄名對齊前端）。"""
    try:
        f = json.loads(r.get("jg_data") or "{}")
    except (ValueError, TypeError):
        f = {}
    return {
        "finding_id": r.get("jg_finding_id"),
        "l1_domain": f.get("l1_domain_code"),
        "l1_label": f.get("l1_label"),
        "l2_code": f.get("l2_code"),
        "l2_label": f.get("l2_label"),
        "l3_code": f.get("l3_code"),
        "l3_label": f.get("l3_label"),
        "confidence": r.get("jg_confidence"),
        "confidence_tier": f.get("confidence_tier"),
        "judgment_stage": f.get("judgment_stage"),
        "recommended_action": f.get("recommended_action"),
        "polarity": f.get("polarity"),
        "problem_summary": f.get("problem_summary"),
        "reason": f.get("reason") or f.get("evidence_quote"),
        "is_primary": f.get("is_primary"),
    }


def _jg_join_cond(spec):
    """judgments 與來源表的複合鍵 join 條件：source + source_id == 該表特徵 id 欄。"""
    jg = T.judgments
    return and_(jg.c.source == spec.source, jg.c.source_id == spec.table.c[spec.natural_key])


def _jg_exists(spec, *extra):
    """`EXISTS (SELECT 1 FROM judgments WHERE source=X AND source_id=特徵id [AND ...])`。"""
    return exists().where(and_(_jg_join_cond(spec), *extra))


def _paged_fanout(spec, apply_filters, sort_expr, sort_dir: str, limit: int, offset: int) -> dict:
    """review-based 分頁 + 多歸因 fan-out（1:N）：先在 item（特徵 id）級分頁取本頁 id，
    再撈這些 item 的**全部**歸因列（judgments 依 (source, source_id) join）→ 每條歸因一列 + span helper。

    分頁固定在 review（特徵 id）級，同 item 歸因永遠同頁連續（避免 join fan-out 跨頁切斷 span 合併）。

    Args:
        spec: 來源 SourceSpec（table + natural_key + source）。
        apply_filters: 把篩選 WHERE 套到傳入 select 的函式（item 級 + 判決 EXISTS）。
        sort_expr: item 級排序運算式（date_col / confidence 的 max 子查詢）。
        sort_dir/limit/offset: 排序方向 + review 級分頁。

    Returns:
        {"rows": [fan-out 列（各附 source_id/finding_id/_group/_rowspan/_seq）], "total": 符合篩選 review 數}。
    """
    jg = T.judgments
    tbl = spec.table
    nk = tbl.c[spec.natural_key]
    src = spec.source
    order_item = (sort_expr.asc() if sort_dir == "asc" else sort_expr.desc()).nullslast()
    id_sel = (
        apply_filters(select(nk).select_from(tbl))
        .order_by(order_item, nk.asc())
        .limit(limit)
        .offset(offset)
    )
    count_sel = apply_filters(select(func.count()).select_from(tbl))
    with T.get_engine().connect() as c:
        total = c.execute(count_sel).scalar() or 0
        item_ids = [r[0] for r in c.execute(id_sel)]
        if not item_ids:
            return {"rows": [], "total": total}
        fan = (
            select(
                tbl,
                jg.c.finding_id.label("jg_finding_id"),
                jg.c.dimension.label("jg_dimension"),
                jg.c.confidence.label("jg_confidence"),
                jg.c.raw_confidence.label("jg_raw_confidence"),
                jg.c.needs_review.label("jg_needs_review"),
                jg.c.data.label("jg_data"),
            )
            .select_from(tbl.outerjoin(jg, _jg_join_cond(spec)))
            .where(nk.in_(item_ids))
            .order_by(order_item, nk.asc(), jg.c.finding_id.asc().nullslast())
        )
        raw = [dict(r) for r in c.execute(fan).mappings()]
    # 依連續相同特徵 id 分組 → **每 review 一列**（review 級欄取首列）+ attributions 陣列（該 review 全部歸因）；
    # _seq＝review 序號。前端右側歸因欄由上至下堆疊 attributions；導出時 fan-out 各歸因並合併 review 級欄。
    rows: list[dict] = []
    i, seq = 0, offset
    while i < len(raw):
        k = i
        sid = raw[i].get(spec.natural_key)
        while k < len(raw) and raw[k].get(spec.natural_key) == sid:
            k += 1
        seq += 1
        row = _enrich_problem(raw[i], src)  # review 級 + primary 歸因相容欄（取首列）
        row["_group"] = sid
        row["_seq"] = seq
        row["attributions"] = [_attribution_of(r) for r in raw[i:k] if r.get("jg_finding_id")]
        rows.append(row)
        i = k
    return {"rows": rows, "total": total}


def list_problems(
    source: str | None = None,
    judged: bool | None = None,
    polarity: str | None = None,
    stage: str | None = None,
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
    # 5 來源全部拆表 → 一律走 spec 路徑；source=None（縱覽全部）無單表可查故回空
    # （刻意不做跨 5 表 UNION——縱覽統計走 attribution_overview/breakdown）。
    spec = source_registry.spec_for(source)
    if spec is None:
        return {"rows": [], "total": 0}
    return _list_problems_spec(
        spec, judged, polarity, stage, limit, offset, score, product_vertical, date_from, date_to,
        date_field, prod_oid, order_oid, sort_by, sort_dir,
    )


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


def _vertical_scoped_spec(
    source: str | None, product_vertical: str | list[str] | None
) -> source_registry.SourceSpec | None:
    """歸因聚合（overview/breakdown）選表：source 命中拆表來源用其 spec；否則 source=None（縱覽全部）
    但帶商品垂直分類篩選時，改走唯一具分類欄的 product_reviews。

    縱覽走 intake_items（全部來源）本身無商品分類欄，結構上無法套垂直分類過濾；為使「嚴格限制在
    商品垂直分類內」在縱覽亦生效（用戶要求），有篩選時改由 product_reviews 專表聚合——即縱覽套分類
    時只統計「有分類且落在所選分類」的資料，無分類來源（進線/工單）在有篩選時排除。無篩選則回 None，
    呼叫端 fallback intake_items 維持「全部來源」語義不變。
    """
    spec = source_registry.spec_for(source)
    if spec is None and _vertical_codes(product_vertical):
        spec = source_registry.spec_for("product_reviews")
    return spec


def _list_problems_spec(
    spec: source_registry.SourceSpec,
    judged: bool | None,
    polarity: str | None,
    stage: str | None,
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
    # 日期欄：canonical 'go_date' 且該表有 lst_dt_go → 用之；否則一律 spec.date_col（occurred_at 等價源欄）
    date_col = tbl.c["lst_dt_go"] if (date_field == "go_date" and "lst_dt_go" in tbl.c) else tbl.c[spec.date_col]

    def _f(stmt):
        """spec 分支篩選：score/vertical/日期/prod_oid/order_oid（表級）+ judged/polarity/stage（判決 EXISTS）。"""
        has_jg = _jg_exists(spec)
        if judged is True:
            stmt = stmt.where(has_jg)
        elif judged is False:
            stmt = stmt.where(~has_jg)
        if polarity:
            stmt = stmt.where(_jg_exists(spec, sa_cast(jg.c.data, JSONB)["polarity"].astext == polarity))
        if stage == "unjudged":
            stmt = stmt.where(~has_jg)
        elif stage:
            stmt = stmt.where(_jg_exists(spec, sa_cast(jg.c.data, JSONB)["judgment_stage"].astext == stage))
        if score and spec.score_col:
            # 源欄為 Text（如 rec_scores="5"）→ 星等清單轉字串比對
            stmt = stmt.where(tbl.c[spec.score_col].in_([str(s) for s in score]))
        if spec.category_col:
            codes = _vertical_codes(product_vertical)
            if codes:
                # product_category 為 raw JSON（{"main":"CATEGORY_..","sub":[]}）→ 抽 main 比對
                stmt = stmt.where(sa_cast(tbl.c[spec.category_col], JSONB)["main"].astext.in_(codes))
        if prod_oid and "prod_oid" in tbl.c:
            stmt = stmt.where(tbl.c.prod_oid == prod_oid)
        if order_oid and "order_oid" in tbl.c:
            stmt = stmt.where(tbl.c.order_oid == order_oid)
        if date_from:
            stmt = stmt.where(func.substr(date_col, 1, 10) >= date_from)
        if date_to:
            stmt = stmt.where(func.substr(date_col, 1, 10) <= date_to)
        return stmt

    # item 級排序（白名單防注入）；confidence 取該 item 各歸因最大信心（scalar 子查詢）
    _sort_map = {
        "occurred_at": tbl.c[spec.date_col],
        "go_date": tbl.c["lst_dt_go"] if "lst_dt_go" in tbl.c else tbl.c[spec.date_col],
        "score": tbl.c[spec.score_col] if spec.score_col else tbl.c[spec.date_col],
    }
    if sort_by == "confidence":
        sort_expr = select(func.max(jg.c.confidence)).where(_jg_join_cond(spec)).scalar_subquery()
    else:
        sort_expr = _sort_map.get(sort_by or "", tbl.c[spec.date_col])
    return _paged_fanout(spec, _f, sort_expr, sort_dir, limit, offset)


# 導出 CSV 欄位（標題, 記錄鍵）；全繁中、不含 L3 code（code 僅存 DB，不對外顯示）
_EXPORT_COLS: list[tuple[str, str]] = [
    ("編號", "source_id"),
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

# 導出 xlsx 欄位（標題, 記錄鍵, 欄寬）：特徵 id（source_id）第一列取代 item_id、加判決階段；1:N 每條歸因一列
_EXPORT_XLSX_COLS: list[tuple[str, str, int]] = [
    ("編號", "source_id", 14),
    ("來源", "source_label", 12),
    ("商品ID", "prod_oid", 12),
    ("商品名稱", "prod_name", 28),
    ("評論", "content", 48),
    ("星等", "score", 8),
    ("評論時間", "occurred_at", 20),
    ("出發日", "go_date", 14),
    ("訂單", "order_mid", 16),
    ("傾向", "polarity", 10),
    ("L1", "l1_label", 14),
    ("L2", "l2_label", 14),
    ("L3", "l3_label", 18),
    ("信心", "confidence", 8),
    ("分層", "confidence_tier", 12),
    ("判決階段", "judgment_stage", 12),
    ("問題摘要", "problem_summary", 40),
    ("依據", "reason", 40),
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
_STAGE_LABEL_ZH = _JUDGMENT_CFG.get("stage_labels", {})
_CONFIDENCE_TIERS = _JUDGMENT_CFG.get(
    "confidence_tiers", {"auto_accept": 0.8, "jury_low": 0.5, "jury_high": 0.7}
)


def _export_cell(key: str, value) -> str:
    """導出單格：時間欄正規化、傾向/分層/判決階段 code→繁中，其餘原樣（None→空字串）。"""
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
    if key == "judgment_stage":
        return _STAGE_LABEL_ZH.get(value, value)
    return value


# openpyxl 禁用的控制字元（\x00-\x08\x0b\x0c\x0e-\x1f）；源資料商品名/評論可能夾帶 → 寫 xlsx 前剔除
_XLSX_ILLEGAL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _xlsx_safe(value):
    """xlsx 格值清洗：str 剔除 openpyxl 非法控制字元（否則 IllegalCharacterError）；非 str 原樣。"""
    return _XLSX_ILLEGAL_RE.sub("", value) if isinstance(value, str) else value


def _export_sheet_title(source: str | None, rows: list[dict], date_from: str | None, date_to: str | None) -> str:
    """工作表名＝來源 label + 時間區間（如「商品評論 20260601~20260701」）。

    時間區間優先取日期篩選 date_from/date_to；未篩選則由匯出資料的 occurred_at 最小/最大值推導。
    Excel 工作表名限制：≤31 字、禁用 : \\ / ? * [ ]（超限/含禁字元會存檔失敗 → 清洗截斷）。
    """
    from app.core import sources as _sources

    label = _sources.label_for(source) if source else "全部來源"

    def _compact(s) -> str:
        """時間字串取前 8 位數字（YYYYMMDD）；無效回空。"""
        d = re.sub(r"\D", "", str(s or ""))
        return d[:8] if len(d) >= 8 else ""

    d1, d2 = _compact(date_from), _compact(date_to)
    if not (d1 and d2):  # 無日期篩選 → 由資料 occurred_at 推區間
        occ = sorted(o for o in (_compact(r.get("occurred_at")) for r in rows) if o)
        if occ:
            d1, d2 = d1 or occ[0], d2 or occ[-1]
    title = f"{label} {d1}~{d2}" if (d1 and d2) else label
    return re.sub(r"[:\\/?*\[\]]", "", title)[:31]


def export_problems_xlsx(
    source: str | None = None,
    polarity: str | None = None,
    judged: bool | None = None,
    item_ids: list[str] | None = None,
    score: list[int] | None = None,
    product_vertical: str | list[str] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> bytes:
    """依篩選/選取導出統一問題列表為**美化 xlsx**（1:N fan-out：每條歸因一列；xid 第一列、不含 item_id）。

    複用 rule_export._style_header（品牌綠表頭/凍結首列+xid 欄/篩選箭頭/斑馬/細邊框），與規則導出視覺一致。
    list_problems 已 fan-out（一則多歸因各一列），逐列寫出；review 級欄在多歸因列重複。傾向/分層/判決階段
    輸出繁中 label。xid＝product_reviews.xid（其他來源無 xid → 用 source_record_id 兜底）。openpyxl lazy import。

    Args:
        source/polarity/judged/score/product_vertical/date_from/date_to: 同 list_problems 篩選（與畫面一致）。
        item_ids: 給定時只導這些 review（前端勾選）；比對 fan-out 列的 _group（item_id）。

    Returns:
        xlsx 位元組（供 API 以 attachment 回傳）。
    """
    from io import BytesIO

    from openpyxl import Workbook

    from app.core.rule_export import _style_header

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
        rows = [r for r in rows if r.get("_group") in idset]
    wb = Workbook()
    ws = wb.active
    ws.title = _export_sheet_title(source, rows, date_from, date_to)
    ws.append([c[0] for c in _EXPORT_XLSX_COLS])
    # 歸因級欄（逐條歸因不同）vs review 級欄（同一 review 相同 → 合併儲存格）
    _attr_keys = {
        "l1_label", "l2_label", "l3_label", "confidence", "confidence_tier",
        "judgment_stage", "problem_summary", "reason",
    }
    review_col_idx = [ci for ci, (_t, key, _w) in enumerate(_EXPORT_XLSX_COLS, start=1) if key not in _attr_keys]
    merges: list[tuple[int, int]] = []  # (起始 Excel 列, 該 review 歸因數 N)
    r_excel = 2  # 資料起始列（表頭列 1）
    for r in rows:
        attrs = r.get("attributions") or []
        n = max(1, len(attrs))
        for j in range(n):
            a = attrs[j] if j < len(attrs) else {}
            line = []
            for _title, key, _w in _EXPORT_XLSX_COLS:
                src_val = a.get(key, "") if key in _attr_keys else r.get(key, "")
                line.append(_xlsx_safe(_export_cell(key, src_val)))
            ws.append(line)
        merges.append((r_excel, n))
        r_excel += n
    _style_header(ws, [c[2] for c in _EXPORT_XLSX_COLS], freeze_cols=1)  # 凍結表頭 + 編號首欄
    # style 後再合併同一 review 的 review 級欄（避免 MergedCell 樣式設定問題）
    for sr, n in merges:
        if n > 1:
            for ci in review_col_idx:
                ws.merge_cells(start_row=sr, start_column=ci, end_row=sr + n - 1, end_column=ci)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


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
        rows = [r for r in rows if r.get("_group") in idset]
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


def prejudge_target_ids(
    source: str | None = None,
    product_vertical: str | list[str] | None = None,
    stages: list[str] | None = None,
    target_polarity: str | None = None,
    max_confidence: float | None = None,
) -> list[str]:
    """初判歸因「目標選取」的 item_id 清單（scope=all；stage 驅動）。

    只 SELECT item_id、不跑 _enrich_problem，避免對全量 intake 做 source_mapping 還原的無謂開銷；
    供 prejudge_batch 批量派工前一次解析標的集合。source 命中 source_registry（product_reviews）
    查專表，否則 fallback intake_items。

    選取邏輯（兩分支聯集去重）：
    - stages 含 'unjudged' → 收「無 finding 列」item_ids（首判，原 unjudged_item_ids 語義）。
    - stages 含已判階段（judged/pending_review/pending_data/insufficient）→ 收
      judgments.data.judgment_stage ∈ 該些階段（JSONB 抽取，複用 list_problems pattern），並可再收斂
      target_polarity（judgments.data.polarity）與 max_confidence（judgments.confidence 結構化欄 < 上限）
      ——供「只重判已判中負向且低信心」等再收斂場景，避免浪費 token 重判已確定的正向/高信心。

    Args:
        source: 來源 code（None＝全部；未拆表來源沿用 intake_items，無分類欄故不套 vertical）。
        product_vertical: 商品垂直分類分組（僅 spec.category_col 存在的來源生效）。
        stages: 目標判決階段清單（預設 ["unjudged"]）。
        target_polarity: 已判分支的傾向收斂（如 "negative"；None＝不收斂）。
        max_confidence: 已判分支的信心上限（confidence < 此值才收；None＝不收斂）。

    Returns:
        目標 item_id 清單（去重；順序不保證，批量判決不依賴順序）。
    """
    stages = stages or ["unjudged"]
    want_unjudged = "unjudged" in stages
    judged_stages = [s for s in stages if s != "unjudged"]
    spec = source_registry.spec_for(source)
    if spec is None:  # 5 來源全拆表；source 必給且須已登記
        return []
    tbl, jg = spec.table, T.judgments
    nk = tbl.c[spec.natural_key]
    j = tbl.outerjoin(jg, _jg_join_cond(spec))

    def _scope(stmt):
        """套商品垂直分類過濾（有分類欄的來源；product_category 為 JSON 抽 main 比對）。"""
        if spec.category_col:
            codes = _vertical_codes(product_vertical)
            if codes:
                stmt = stmt.where(sa_cast(tbl.c[spec.category_col], JSONB)["main"].astext.in_(codes))
        return stmt

    ids: set[str] = set()
    with T.get_engine().connect() as c:
        if want_unjudged:
            s = _scope(select(nk).select_from(j).where(jg.c.finding_id.is_(None)))
            ids.update(r[0] for r in c.execute(s))
        if judged_stages:
            s = select(nk).select_from(j).where(jg.c.finding_id.isnot(None))
            s = s.where(sa_cast(jg.c.data, JSONB)["judgment_stage"].astext.in_(judged_stages))
            if target_polarity:
                s = s.where(sa_cast(jg.c.data, JSONB)["polarity"].astext == target_polarity)
            if max_confidence is not None:
                s = s.where(jg.c.confidence < max_confidence)
            ids.update(r[0] for r in c.execute(_scope(s)))
    return [str(x) for x in ids if x is not None]


def unjudged_item_ids(
    source: str | None = None, product_vertical: str | list[str] | None = None
) -> list[str]:
    """未歸因 item_id 清單（薄封裝 prejudge_target_ids(stages=["unjudged"])；相容既有呼叫）。"""
    return prejudge_target_ids(source, product_vertical, stages=["unjudged"])


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
    # 縱覽（source=None）帶垂直分類篩選時改走 product_reviews（見 _vertical_scoped_spec）。
    spec = _vertical_scoped_spec(source, product_vertical)
    jg = T.judgments
    cnt = func.count().label("n")
    tiers = _CONFIDENCE_TIERS
    # judgments.data JSON 內的歸因欄（JSONB 抽出，供 GROUP BY / FILTER）
    pol = sa_cast(jg.c.data, JSONB)["polarity"].astext
    l1c = sa_cast(jg.c.data, JSONB)["l1_domain_code"].astext
    l1l = sa_cast(jg.c.data, JSONB)["l1_label"].astext
    _GRAN_LEN = {"year": 4, "month": 7, "day": 10}
    glen = _GRAN_LEN.get(granularity, 7)
    _v_codes = _vertical_codes(product_vertical) if (spec is not None and spec.category_col) else []
    _ALL_TABLES = (T.product_reviews, T.conversations, T.freshdesk_tickets, T.app_feedback, T.mixpanel_tracker)

    def _by_tier(conf_rows) -> dict:
        bt = {"auto_accept": 0, "jury": 0, "needs_review": 0}
        for r in conf_rows:
            conf = r["confidence"]
            bt["auto_accept" if conf >= tiers["auto_accept"] else ("jury" if conf >= tiers["jury_low"] else "needs_review")] += 1
        return bt

    with T.get_engine().connect() as c:
        if spec is not None:
            # 單一來源：join 該表（可套 date / vertical / 星等 / 趨勢）
            tbl = spec.table
            date_col = tbl.c[spec.date_col]
            score_col = tbl.c[spec.score_col] if spec.score_col else None
            j = tbl.outerjoin(jg, _jg_join_cond(spec))

            def _src(stmt):  # 套日期區間 + 商品垂直分類（None／空＝不限）
                if date_from:
                    stmt = stmt.where(func.substr(date_col, 1, 10) >= date_from)
                if date_to:
                    stmt = stmt.where(func.substr(date_col, 1, 10) <= date_to)
                if _v_codes:
                    stmt = stmt.where(sa_cast(tbl.c[spec.category_col], JSONB)["main"].astext.in_(_v_codes))
                return stmt

            total_intake = c.execute(_src(select(cnt).select_from(tbl))).scalar() or 0
            judged = c.execute(_src(select(cnt).select_from(j).where(jg.c.finding_id.isnot(None)))).scalar() or 0
            attributed = c.execute(_src(select(cnt).select_from(j).where(l1c.isnot(None), l1c != ""))).scalar() or 0
            by_polarity_raw = c.execute(_src(select(pol.label("k"), cnt).select_from(j).where(jg.c.finding_id.isnot(None)).group_by(pol).order_by(cnt.desc()))).mappings().all()
            by_l1_raw = c.execute(_src(select(l1c.label("code"), l1l.label("label"), cnt).select_from(j).where(l1c.isnot(None), l1c != "").group_by(l1c, l1l).order_by(cnt.desc()))).mappings().all()
            by_score_raw = (
                c.execute(_src(select(score_col.label("score"), cnt).select_from(tbl).where(score_col.isnot(None)).group_by(score_col).order_by(score_col.asc()))).mappings().all()
                if score_col is not None else []
            )
            by_tier = _by_tier(c.execute(_src(select(jg.c.confidence).select_from(j).where(jg.c.confidence.isnot(None)))).mappings())
            ym = func.substr(date_col, 1, glen).label("ym")
            trend_rows = c.execute(_src(
                select(ym, func.count(jg.c.finding_id).label("judged"), func.count().filter(pol == "negative").label("negative"))
                .select_from(j).where(date_col.isnot(None), date_col != "", jg.c.finding_id.isnot(None)).group_by(ym).order_by(ym.asc())
            )).mappings().all()
        else:
            # 縱覽（source=None，無 vertical）：judgments 直接聚合（含全 5 來源）；total_intake=5 表和；無 date/星等/趨勢
            total_intake = sum((c.execute(select(func.count()).select_from(t)).scalar() or 0) for t in _ALL_TABLES)
            judged = c.execute(select(cnt).select_from(jg)).scalar() or 0
            attributed = c.execute(select(cnt).select_from(jg).where(l1c.isnot(None), l1c != "")).scalar() or 0
            by_polarity_raw = c.execute(select(pol.label("k"), cnt).select_from(jg).group_by(pol).order_by(cnt.desc())).mappings().all()
            by_l1_raw = c.execute(select(l1c.label("code"), l1l.label("label"), cnt).select_from(jg).where(l1c.isnot(None), l1c != "").group_by(l1c, l1l).order_by(cnt.desc())).mappings().all()
            by_score_raw = []
            by_tier = _by_tier(c.execute(select(jg.c.confidence).select_from(jg).where(jg.c.confidence.isnot(None))).mappings())
            trend_rows = []

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
    # 縱覽（source=None）帶垂直分類篩選時改走 product_reviews（見 _vertical_scoped_spec）。
    spec = _vertical_scoped_spec(source, product_vertical)
    jg = T.judgments
    cnt = func.count().label("n")
    d = sa_cast(jg.c.data, JSONB)
    l1c, l1l = d["l1_domain_code"].astext, d["l1_label"].astext
    l2c, l2l = d["l2_code"].astext, d["l2_label"].astext
    l3c, l3l = d["l3_code"].astext, d["l3_label"].astext
    _v_codes = _vertical_codes(product_vertical) if (spec is not None and spec.category_col) else []

    # spec 命中：join 該表（可套 date/vertical）；source=None：judgments 直接聚合
    if spec is not None:
        tbl = spec.table
        date_col = tbl.c[spec.date_col]
        frm = tbl.outerjoin(jg, _jg_join_cond(spec))
        extra = []
        if date_from:
            extra.append(func.substr(date_col, 1, 10) >= date_from)
        if date_to:
            extra.append(func.substr(date_col, 1, 10) <= date_to)
        if _v_codes:
            extra.append(sa_cast(tbl.c[spec.category_col], JSONB)["main"].astext.in_(_v_codes))
    else:
        frm = jg
        extra = []

    def _level(code_col, label_col):
        """組某層（L2/L3）GROUP BY：限定 L1 域 + 非空 code + 篩選，依筆數降序（1:N 下即歸因次數 fan-out）。"""
        stmt = (
            select(code_col.label("code"), label_col.label("label"), cnt)
            .select_from(frm)
            .where(l1c == l1, code_col.isnot(None), code_col != "")
        )
        for w in extra:
            stmt = stmt.where(w)
        return stmt.group_by(code_col, label_col).order_by(cnt.desc())

    with T.get_engine().connect() as c:
        l1_label = (
            c.execute(select(l1l).select_from(frm).where(l1c == l1, l1l.isnot(None)).limit(1)).scalar()
            or l1
        )
        by_l2 = [dict(r) for r in c.execute(_level(l2c, l2l)).mappings()]
        by_l3 = [dict(r) for r in c.execute(_level(l3c, l3l)).mappings()]
    return {"l1_code": l1, "l1_label": l1_label, "by_l2": by_l2, "by_l3": by_l3}

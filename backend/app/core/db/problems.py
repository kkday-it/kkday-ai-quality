"""統一問題列表（來源專表 LEFT JOIN judgments；公共欄由 source_mapping 於回傳層還原）+ 多歸因 fan-out。"""

from __future__ import annotations

import json

from sqlalchemy import cast as sa_cast
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import JSONB

from app.core.db import source_registry
from app.core.db import tables as T
from app.core.db._shared import (
    _jg_exists,
    _jg_join_cond,
    _vertical_codes,
    attribution_dto,
)

# fan-out 需帶回的 judgments typed 判決欄（以 jg_ 前綴 label，避免與來源表欄名撞）。
_JG_COLS = (
    "finding_id", "polarity", "stage", "l1_code", "l1_label", "l2_code", "l2_label",
    "l3_code", "l3_label", "conf_value", "conf_raw", "conf_tier", "summary", "evidence",
    "action", "is_primary", "status", "true_label",
)


def _jg_unwrap(r: dict) -> dict:
    """fan-out 列（jg_ 前綴判決欄）→ 無前綴 dict（供 attribution_dto）。"""
    return {k: r.get(f"jg_{k}") for k in _JG_COLS}


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
    source_mapping.normalize_row(source, row)（源欄→canonical）產出。
    `source_id`＝該表特徵 id（spec.natural_key 欄值）；`item_id` 為傳輸/顯示相容字串 `{source}:{source_id}`。
    """
    from app.core import source_mapping as _srcmap
    from app.core import sources as _sources

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

    # review 級判決摘要欄（詳細 L1-L3/信心/摘要走 attributions[] nested DTO，此處僅留列渲染/篩選/導出用）
    base.update(
        {
            "judged": bool(row.get("jg_finding_id")),
            "needs_review": bool(row.get("jg_needs_review")),
            "polarity": row.get("jg_polarity"),  # 列級傾向（前端列樣式 record.polarity + 導出「傾向」欄）
            "dimension": row.get("jg_dimension"),
        }
    )
    return base


def _derive_stage(dto: dict) -> str:
    """階段派生（僅供 stage 欄空的 legacy 列相容顯示；新資料 stage 欄已存值）。

    dto＝attribution_dto 產物（巢狀）。負向且無 L3→pending_data；auto_accept→judged 否則
    pending_review；unknown→insufficient；正/中→judged。
    """
    pol = dto.get("polarity")
    if pol == "unknown":
        return "insufficient"
    if pol != "negative":
        return "judged"
    if not (dto.get("l3") or {}).get("code"):
        return "pending_data"
    return "judged" if (dto.get("confidence") or {}).get("tier") == "auto_accept" else "pending_review"


def _attribution_of(r: dict) -> dict:
    """單筆 judgments fan-out 列（jg_ 前綴 typed 欄）→ 一條歸因的乾淨巢狀 DTO（供列表堆疊 / 導出）。"""
    dto = attribution_dto(_jg_unwrap(r))
    if dto["finding_id"] and not dto["stage"]:  # legacy 空 stage 相容派生
        dto["stage"] = _derive_stage(dto)
    return dto


def _paged_fanout(spec, apply_filters, sort_expr, sort_dir: str, limit: int, offset: int) -> dict:
    """review-based 分頁 + 多歸因 fan-out（1:N）：先在 item（特徵 id）級分頁取本頁 id，
    再撈這些 item 的**全部**歸因列（judgments 依 (source, source_id) join）→ 每 review 一列 + attributions 陣列。

    分頁固定在 review（特徵 id）級，同 item 歸因永遠同頁連續。

    Returns:
        {"rows": [每 review 一列（附 _group/_seq/attributions）], "total": 符合篩選 review 數}。
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
                *[jg.c[k].label(f"jg_{k}") for k in _JG_COLS],  # typed 判決欄（含 status/true_label）
                jg.c.needs_review.label("jg_needs_review"),
                jg.c.dimension.label("jg_dimension"),
            )
            .select_from(tbl.outerjoin(jg, _jg_join_cond(spec)))
            .where(nk.in_(item_ids))
            .order_by(order_item, nk.asc(), jg.c.finding_id.asc().nullslast())
        )
        raw = [dict(r) for r in c.execute(fan).mappings()]
    # 依連續相同特徵 id 分組 → 每 review 一列（review 級欄取首列）+ attributions 陣列（該 review 全部歸因）。
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
    stage: list[str] | None = None,
    limit: int = 100,
    offset: int = 0,
    score: list[int] | None = None,
    product_vertical: str | list[str] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    date_field: str = "occurred_at",
    rec_oid: str | None = None,
    prod_oid: str | None = None,
    order_oid: str | None = None,
    confidence_tier: str | None = None,
    l1_domain: str | None = None,
    sort_by: str | None = None,
    sort_dir: str = "desc",
) -> dict:
    """統一問題列表（來源專表 LEFT JOIN judgments），分頁。回 {rows, total}。

    5 來源皆已拆獨立表：source 命中 source_registry 時查該專表（表本身即單一來源，免 WHERE source=）。
    **不做跨表 UNION**——source=None（縱覽全部）無單表可查，直接回空 {rows:[], total:0}
    （縱覽聚合走 attribution_overview/breakdown 的 judgments 直接聚合，非此列表）。

    Args:
        source: 來源 code 過濾（product_reviews…）。
        judged: True=僅已歸因 / False=僅未歸因 / None=全部。
        polarity: 傾向過濾（judgments.data.polarity）。
        stage: 判決階段多選（judgments.data.judgment_stage；'unjudged'＝無判決，多值 OR）。
        limit/offset: 分頁。
        score: 星等過濾（IN 清單；僅 source_registry 命中且有 score_col 的來源可用）。
        product_vertical: 商品垂直分類名（單一或清單；經 product_vertical.codes_for_group 展開為 CATEGORY 代碼）。
        date_from/date_to: 日期區間（'YYYY-MM-DD'，含端點）；比對 date_field 前 10 字。
        date_field: 日期篩選欄名（'occurred_at' | 'go_date'；僅 source_registry 命中的表可用）。
        confidence_tier: 信心分層過濾（judgments.data.confidence_tier；auto_accept/jury/needs_review）。
        l1_domain: L1 歸因域過濾（judgments.data.l1_domain_code；content/supplier/…）。

    Returns:
        {"rows": [統一記錄], "total": 符合篩選總數}。
    """
    spec = source_registry.spec_for(source)
    if spec is None:
        return {"rows": [], "total": 0}
    return _list_problems_spec(
        spec, judged, polarity, stage, limit, offset, score, product_vertical, date_from, date_to,
        date_field, rec_oid, prod_oid, order_oid, confidence_tier, l1_domain, sort_by, sort_dir,
    )


def _list_problems_spec(
    spec: source_registry.SourceSpec,
    judged: bool | None,
    polarity: str | None,
    stage: list[str] | None,
    limit: int,
    offset: int,
    score: list[int] | None,
    product_vertical: str | list[str] | None,
    date_from: str | None,
    date_to: str | None,
    date_field: str,
    rec_oid: str | None = None,
    prod_oid: str | None = None,
    order_oid: str | None = None,
    confidence_tier: str | None = None,
    l1_domain: str | None = None,
    sort_by: str | None = None,
    sort_dir: str = "desc",
) -> dict:
    """list_problems 的已拆表來源分支：直接查該專表 LEFT JOIN judgments。

    表本身即單一來源，無需 WHERE source= 過濾；score/product_vertical/日期區間為此分支專屬篩選。
    """
    tbl = spec.table
    # 日期欄：canonical 'go_date' 且該表有 lst_dt_go → 用之；否則一律 spec.date_col（occurred_at 等價源欄）
    date_col = tbl.c["lst_dt_go"] if (date_field == "go_date" and "lst_dt_go" in tbl.c) else tbl.c[spec.date_col]

    def _f(stmt):
        """spec 分支篩選：score/vertical/日期/prod_oid/order_oid（表級）+ judged/polarity/stage（判決 EXISTS）。"""
        has_jg = _jg_exists(spec)
        if judged is True:
            stmt = stmt.where(has_jg)
        elif judged is False:
            stmt = stmt.where(~has_jg)
        jg = T.judgments
        if polarity:
            stmt = stmt.where(_jg_exists(spec, jg.c.polarity == polarity))
        if stage:
            # 多選階段：'unjudged'＝無判決(NOT EXISTS)，其餘＝stage IN；兩者 OR 併存
            conds = []
            if "unjudged" in stage:
                conds.append(~has_jg)
            judged_stages = [s for s in stage if s != "unjudged"]
            if judged_stages:
                conds.append(_jg_exists(spec, jg.c.stage.in_(judged_stages)))
            if conds:
                stmt = stmt.where(or_(*conds))
        if confidence_tier:
            stmt = stmt.where(_jg_exists(spec, jg.c.conf_tier == confidence_tier))
        if l1_domain:
            stmt = stmt.where(_jg_exists(spec, jg.c.l1_code == l1_domain))
        if score and spec.score_col:
            # 源欄為 Text（如 rec_scores="5"）→ 星等清單轉字串比對
            stmt = stmt.where(tbl.c[spec.score_col].in_([str(s) for s in score]))
        if spec.category_col:
            codes = _vertical_codes(product_vertical)
            if codes:
                # product_category 為 raw JSON（{"main":"CATEGORY_..","sub":[]}）→ 抽 main 比對
                stmt = stmt.where(sa_cast(tbl.c[spec.category_col], JSONB)["main"].astext.in_(codes))
        if rec_oid and spec.natural_key in tbl.c:
            # rec_oid＝評論 id（各來源表 natural_key，product_reviews→rec_oid）；查來源表本身主鍵欄
            stmt = stmt.where(tbl.c[spec.natural_key] == rec_oid)
        if prod_oid and "prod_oid" in tbl.c:
            stmt = stmt.where(tbl.c.prod_oid == prod_oid)
        if order_oid and "order_oid" in tbl.c:
            stmt = stmt.where(tbl.c.order_oid == order_oid)
        # sargable 日期比較（走 btree 索引，取代打死索引的 substr）；date_col 為 raw datetime 文字，
        # 上界半開 < date_to||'~' 以含當日整天（直接 <= 會漏當日有時間的列），'~' 大於 ' '/'T' 分隔符。
        if date_from:
            stmt = stmt.where(date_col >= date_from)
        if date_to:
            stmt = stmt.where(date_col < date_to + "~")
        return stmt

    # item 級排序（白名單防注入）；confidence 取該 item 各歸因最大信心（scalar 子查詢）
    _sort_map = {
        "occurred_at": tbl.c[spec.date_col],
        "go_date": tbl.c["lst_dt_go"] if "lst_dt_go" in tbl.c else tbl.c[spec.date_col],
        "score": tbl.c[spec.score_col] if spec.score_col else tbl.c[spec.date_col],
    }
    if sort_by == "confidence":
        # 該 item 各歸因最大信心的 scalar 子查詢。_paged_fanout 外層也 join judgments，若不指定關聯範圍，
        # SQLAlchemy 會把子查詢的 judgments 也 auto-correlate 掉 → 「no FROM clauses」500。
        # correlate_except(judgments)：judgments 留在子查詢 FROM，只把外層 source 表關聯進來。
        sort_expr = (
            select(func.max(T.judgments.c.conf_value))
            .where(_jg_join_cond(spec))
            .correlate_except(T.judgments)
            .scalar_subquery()
        )
    else:
        sort_expr = _sort_map.get(sort_by or "", tbl.c[spec.date_col])
    return _paged_fanout(spec, _f, sort_expr, sort_dir, limit, offset)


def list_l1_domains(source: str) -> list[dict]:
    """某來源已判資料中出現過的 L1 歸因域清單（供列表 L1 篩選下拉，選項恆與資料一致）。

    直接對 judgments.data 抽 distinct (l1_domain_code, l1_label)——label 與 code 同存於判決 JSON，
    故無需另維護 code→label 對照表（SSOT 即資料本身）。按出現次數 desc 排序，空 code 剔除。

    Args:
        source: 來源 code（judgments.source 過濾）。

    Returns:
        [{"code", "label", "count"}]（count＝該域歸因筆數）。
    """
    jg = T.judgments
    code = jg.c.l1_code
    label = jg.c.l1_label
    stmt = (
        select(code.label("code"), label.label("label"), func.count().label("count"))
        .where(jg.c.source == source, code != "", code.isnot(None))
        .group_by(code, label)
        .order_by(func.count().desc())
    )
    with T.get_engine().connect() as conn:
        return [{"code": r.code, "label": r.label, "count": r.count} for r in conn.execute(stmt)]

"""統一問題列表（來源專表 LEFT JOIN attributions；公共欄由 source_mapping 於回傳層還原）+ 多歸因 fan-out。"""

from __future__ import annotations

import json

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy import false as sa_false
from sqlalchemy import true as sa_true

from app.core.db import source_registry
from app.core.db import tables as T
from app.core.db._shared import (
    _jg_exists,
    _jg_join_cond,
    apply_table_filters,
    attribution_dto,
    human_touched_cond,
)

# fan-out 需帶回的 attributions typed 初判欄（以 jg_ 前綴 label，避免與來源表欄名撞）。
_JG_COLS = (
    "attribution_oid",
    "polarity",
    "sentiment_score",
    "prejudge_stage",
    "l1_code",
    "l1_label",
    "l2_code",
    "l2_label",
    "conf_value",
    "conf_raw",
    "conf_tier",
    "summary",
    "evidence",
    "action",
    "model",
    "is_primary",
    "is_auto_accepted",
    # 人工介入欄（is_deleted 刻意不帶：tombstone 已被讀取層過濾，不會出現在 fan-out 結果裡）
    "is_manual_created",
    "is_human_corrected",
    "correction_reason",
    "review_status",
    "modify_user",
    "modify_date",
)


def _jg_unwrap(r: dict) -> dict:
    """fan-out 列（jg_ 前綴初判欄）→ 無前綴 dict（供 attribution_dto）。"""
    return {k: r.get(f"jg_{k}") for k in _JG_COLS}


def _extract_prod_name(raw: dict) -> str:
    """從 raw 的 order_snap_json（多語商品名快照 JSON）取商品名：各語系優先序 zh-tw/zh-hk/zh-cn/en，任一語系兜底。"""
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


def _parse_free_tag(value) -> list[dict]:
    """free_tag 欄（JSON 字串）→ 標籤 dict 清單；tag_list 為內嵌 JSON 字串需二次 parse。

    輸出 [{tag_name, tag_value, tag_list:[詞,...]}]；任一層 parse 失敗回 []（輔助訊號，壞值不阻列表）。
    """
    if not value:
        return []
    try:
        items = json.loads(value) if isinstance(value, str) else value
        if not isinstance(items, list):
            return []
        out = []
        for it in items:
            if not isinstance(it, dict):
                continue
            words = it.get("tag_list")
            if isinstance(words, str):  # 導出時 STRING 內嵌 JSON 陣列 → 二次 parse
                try:
                    words = json.loads(words)
                except (ValueError, TypeError):
                    words = []
            out.append(
                {
                    "tag_name": it.get("tag_name"),
                    "tag_value": it.get("tag_value"),
                    "tag_list": words if isinstance(words, list) else [],
                }
            )
        return out
    except (ValueError, TypeError):
        return []


def _enrich_problem(row: dict, source: str | None = None) -> dict:
    """來源表列 × judgment join 列 → 統一問題列表記錄（canonical 顯示欄 + 歸因）。

    5 來源統一：row 為該來源表列（源欄名）+ jg_* 標籤欄；canonical 顯示欄一律經
    source_mapping.normalize_row(source, row)（源欄→canonical）產出。
    `source_id`＝該表特徵 id（spec.natural_key 欄值）；`item_id` 為傳輸/顯示相容字串 `{source}:{source_id}`。
    """
    from app.core import bd_tag_vertical as _bd_tag_vertical
    from app.core import source_mapping as _srcmap
    from app.core import sources as _sources

    src = source or row.get("source") or ""
    spec = source_registry.spec_for(src)
    canon = _srcmap.normalize_row(src, row) if src in _srcmap.sources() else {}
    source_id = row.get(spec.natural_key) if spec else canon.get("source_record_id")
    # 商品名：reviews.order_snap_json（多語快照 JSON）/ conversations.product_name
    snap = row.get("order_snap_json")
    base = {
        "source_id": source_id,
        # 傳輸/顯示相容鍵（前端 rowKey 退回 / 導出 / selectedKeys 用單一字串；派生自 source_id）
        "item_id": f"{src}:{source_id}" if source_id is not None else None,
        "source": src,
        "source_label": _sources.label_for(src),
        "prod_oid": canon.get("prod_oid") or "",
        "prod_name": _extract_prod_name({"order_snap_json": snap})
        if snap
        else (row.get("product_name") or ""),
        "package_name": _extract_package_name({"order_snap_json": snap}) if snap else "",
        "pkg_oid": canon.get("pkg_oid") or "",
        "content": canon.get("content") or "",
        "score": canon.get("score"),
        "occurred_at": canon.get("occurred_at"),
        "title": canon.get("title"),
        "channel": canon.get("channel"),
        "lang": canon.get("lang"),
        "order_oid": canon.get("order_oid"),
        "order_mid": row.get(
            "order_mid"
        ),  # 同名源欄（pr/conv/mixpanel 有；freshdesk/appf 無→None）
        "supplier_oid": canon.get("supplier_oid"),
        "supplier_name": canon.get("supplier_name"),
        # 進線特有欄（trip_stage/go_date 走 canonical field_map；其餘 conversations 專屬源欄直讀，
        # 比照 order_mid 既有作法；其他來源無此欄 → None，前端有值才顯示）
        "trip_stage": canon.get("trip_stage"),
        "go_date": canon.get("go_date"),
        "member_uuid": row.get("member_uuid"),
        "bucket": row.get("bucket"),  # 分桶字面值（BQ 端預算）
        "msg_handler_bucket": row.get("msg_handler_bucket"),  # 處理方：KKDAY/SUPPLIER
        "godate_diff": row.get("godate_diff"),  # 出發日差字面值
        "order_status_now": row.get("order_status_now"),
        "order_lang": row.get("order_lang"),
        "order_price": row.get("order_price"),
        "order_profit": row.get("order_profit"),
        "order_create_source_code": row.get("order_create_source_code"),
        "order_create_time": row.get("order_create_time"),
        "product_tz": row.get("product_tz"),
        # vertical/PM：conversations 由 BQ 端預算好的字面值；reviews 無此欄，落
        # bd_tag_cd 代碼後查 bd_tag_vertical 版本化規則 fallback（DB 版本化，可在配置抽屜編輯）
        "vertical": row.get("vertical")
        or _bd_tag_vertical.vertical_for(row.get("bd_tag_cd") or ""),
        "bd_tag_cd": row.get("bd_tag_cd"),  # BD 分工代碼（兩來源同名，篩選鍵）
        "bd_tag": row.get("bd_tag"),  # BD tag 中文
        "PM": row.get("PM") or _bd_tag_vertical.pm_for(row.get("bd_tag_cd") or ""),
        "cs_tag_oid": row.get("cs_tag_oid"),
        "cs_tag_name": row.get("cs_tag_name"),
        "user_message_count": row.get("user_message_count"),
        "traveller_type": canon.get("traveller_type"),
        "source_record_id": source_id,  # 評論ID（＝特徵 id）
        # 外部評論融合欄（僅 reviews 有；輔助訊號——傾向/歸因以原文 LLM 判定為準）
        "ext_lst_oid": row.get("review_external_lst_oid"),
        "ext_sentiment": row.get("sentiment"),
        "ext_free_tag": _parse_free_tag(row.get("free_tag")),
        "created_at": None,
    }

    # review 級初判摘要欄（詳細 L1-L3/信心/摘要走 attributions[] nested DTO，此處僅留列渲染/篩選/導出用）
    base.update(
        {
            "judged": bool(row.get("jg_attribution_oid")),
            "polarity": row.get(
                "jg_polarity"
            ),  # 列級傾向（前端列樣式 record.polarity + 導出「傾向」欄）
            "our_sentiment": row.get(
                "jg_sentiment_score"
            ),  # 列級我方情緒分 1-5（傾向細分顯示 + 評論對比表；與外部 sentiment 同尺度）
        }
    )
    return base


def _derive_stage(dto: dict) -> str:
    """階段派生（僅供 stage 欄空的 legacy 列相容顯示；新資料 stage 欄已存值）。

    dto＝attribution_dto 產物（巢狀）。負向且無 L3→pending_data；auto_accept→judged 否則
    pending_review；正/中→judged。
    """
    pol = dto.get("polarity")
    if pol != "negative":
        return "judged"
    if not (dto.get("l2") or {}).get("code"):
        return "pending_data"
    return (
        "judged" if (dto.get("confidence") or {}).get("tier") == "auto_accept" else "pending_review"
    )


def _attribution_of(r: dict) -> dict:
    """單筆 attributions fan-out 列（jg_ 前綴 typed 欄）→ 一條歸因的乾淨巢狀 DTO（供列表堆疊 / 導出）。"""
    unwrapped = _jg_unwrap(r)
    dto = attribution_dto(unwrapped)
    if dto["attribution_oid"] and not dto["stage"]:  # legacy 空 stage 相容派生
        dto["stage"] = _derive_stage(dto)
    return dto


def _paged_fanout(spec, apply_filters, sort_expr, sort_dir: str, limit: int, offset: int) -> dict:
    """review-based 分頁 + 多歸因 fan-out（1:N）：先在 item（特徵 id）級分頁取本頁 id，
    再撈這些 item 的**全部**歸因列（attributions 依 (source, source_id) join）→ 每 review 一列 + attributions 陣列。

    分頁固定在 review（特徵 id）級，同 item 歸因永遠同頁連續。

    Returns:
        {"rows": [每 review 一列（附 _group/_seq/attributions）], "total": 符合篩選 review 數}。
    """
    jg = T.attributions
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
                *[jg.c[k].label(f"jg_{k}") for k in _JG_COLS],  # typed 初判欄
            )
            .select_from(tbl.outerjoin(jg, _jg_join_cond(spec)))
            .where(nk.in_(item_ids))
            .order_by(order_item, nk.asc(), jg.c.attribution_oid.asc().nullslast())
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
        row["attributions"] = [_attribution_of(r) for r in raw[i:k] if r.get("jg_attribution_oid")]
        rows.append(row)
        i = k
    return {"rows": rows, "total": total}


def list_problems(
    source: str | None = None,
    judged: bool | None = None,
    polarity: str | list[str] | None = None,
    sentiment: list[int] | None = None,
    stage: list[str] | None = None,
    limit: int = 100,
    offset: int = 0,
    vertical: str | list[str] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    date_field: str = "occurred_at",
    rec_oid: str | None = None,
    prod_oid: str | None = None,
    order_oid: str | None = None,
    confidence_tier: str | None = None,
    taxonomy: list[str] | None = None,
    model: list[str] | None = None,
    has_external: bool | None = None,
    bucket: list[str] | None = None,
    sort_by: str | None = None,
    sort_dir: str = "desc",
    human_state: str | None = None,
) -> dict:
    """統一問題列表（來源專表 LEFT JOIN attributions），分頁。回 {rows, total}。

    5 來源皆已拆獨立表：source 命中 source_registry 時查該專表（表本身即單一來源，免 WHERE source=）。
    **不做跨表 UNION**——source=None（縱覽全部）無單表可查，直接回空 {rows:[], total:0}
    （縱覽聚合走 attribution_overview/breakdown 的 attributions 直接聚合，非此列表）。

    Args:
        source: 來源 code 過濾（reviews…）。
        judged: True=僅已歸因 / False=僅未歸因 / None=全部。
        polarity: 傾向過濾（attributions.data.polarity）。
        stage: 初判階段多選（attributions.data.prejudge_stage；'unjudged'＝無初判，多值 OR）。
        limit/offset: 分頁。
        vertical: 商品垂直分類名（單一或清單；經 bd_tag_vertical.codes_for_vertical 展開為 bd_tag 代碼）。
        date_from/date_to: 日期區間（'YYYY-MM-DD'，含端點）；比對 date_field 前 10 字。
        date_field: 日期篩選欄名（'occurred_at' | 'go_date'；僅 source_registry 命中的表可用）。
        confidence_tier: 信心分層過濾（attributions.data.confidence_tier；auto_accept/jury/needs_review）。
        taxonomy: 歸因分類過濾（任意層級 code 多選；l1/l2_code 任一 IN 命中＝子樹語義）。
        model: 初判模型多選（attributions.model IN——當前初判維度；任一歸因命中即列出）。
        has_external: 有無外部評論融合資料（True=有 / False=無 / None=全部；僅 reviews 表有欄，其餘來源忽略）。
        bucket: 進線分桶多選（conversations 專屬直欄；transferred/chatbot_only/human_supplier/
            human_kkday/human_other；其餘來源無此欄，忽略）。

    Returns:
        {"rows": [統一記錄], "total": 符合篩選總數}。
    """
    spec = source_registry.spec_for(source)
    if spec is None:
        return {"rows": [], "total": 0}
    return _list_problems_spec(
        spec,
        judged,
        polarity,
        stage,
        limit,
        offset,
        vertical,
        date_from,
        date_to,
        date_field,
        rec_oid,
        prod_oid,
        order_oid,
        confidence_tier,
        taxonomy,
        sort_by,
        sort_dir,
        has_external=has_external,
        sentiment=sentiment,
        model=model,
        bucket=bucket,
        human_state=human_state,
    )


def _list_problems_spec(
    spec: source_registry.SourceSpec,
    judged: bool | None,
    polarity: str | list[str] | None,
    stage: list[str] | None,
    limit: int,
    offset: int,
    vertical: str | list[str] | None,
    date_from: str | None,
    date_to: str | None,
    date_field: str,
    rec_oid: str | None = None,
    prod_oid: str | None = None,
    order_oid: str | None = None,
    confidence_tier: str | None = None,
    taxonomy: list[str] | None = None,
    sort_by: str | None = None,
    sort_dir: str = "desc",
    has_external: bool | None = None,
    sentiment: list[int] | None = None,
    model: list[str] | None = None,
    bucket: list[str] | None = None,
    human_state: str | None = None,
) -> dict:
    """list_problems 的已拆表來源分支：直接查該專表 LEFT JOIN attributions。

    表本身即單一來源，無需 WHERE source= 過濾；vertical/日期區間為此分支專屬篩選。
    """
    tbl = spec.table

    def _f(stmt):
        """spec 分支篩選：表級（vertical/日期/oid，SSOT＝_shared.apply_table_filters，與初判
        目標選取共用同一份語義）+ judged/polarity/stage/tier/歸因分類（初判 EXISTS，列表專屬結構）。"""
        # 「這則反饋判過沒有」＝ **ever 口徑（含 tombstone）**，與 prejudge_targets 同一把尺。
        # 人工標記為 AI 誤判代表「AI 判過、人說判錯了」——那仍然是判過。用 live 口徑的話，
        # 歸因全被標記誤判的反饋會在列表顯示「未初判」，但按下初判分類時目標數是 0
        # （prejudge_targets 認為它判過），使用者看到的是兩個互相矛盾的數字。
        # ⚠️ 只有這個「判過沒有」的問題用 ever；下方 polarity / sentiment / stage / tier
        # 等**歸因層**條件一律維持 live 口徑（tombstone 的值不該被篩選命中）。
        ever_judged = _jg_exists(spec, include_deleted=True)
        if judged is True:
            stmt = stmt.where(ever_judged)
        elif judged is False:
            stmt = stmt.where(~ever_judged)
        jg = T.attributions
        if polarity:
            # 傾向多選（positive/neutral/negative）；直接按 attributions.polarity 篩
            pol_list = [polarity] if isinstance(polarity, str) else polarity
            stmt = stmt.where(_jg_exists(spec, jg.c.polarity.in_(pol_list)))
        if sentiment:
            # 情緒分多選（1-5；我方 sentiment_score 由 polarity 確定性映射 正5/中3/負1）
            stmt = stmt.where(_jg_exists(spec, jg.c.sentiment_score.in_(sentiment)))
        if stage:
            # 多選階段：'unjudged'＝從未判過（ever 口徑的 NOT EXISTS，同上），其餘＝stage IN；兩者 OR 併存。
            # ⚠️ 歸因全被標記誤判的反饋兩邊都不match（它判過、又沒有存活的階段值）——那是刻意的：
            # 它的狀態是 judge_state='dismissed'，靠列上的判定狀態或 human_state 篩選找，不塞進階段軸。
            conds = []
            if "unjudged" in stage:
                conds.append(~_jg_exists(spec, include_deleted=True))
            judged_stages = [s for s in stage if s != "unjudged"]
            if judged_stages:
                conds.append(_jg_exists(spec, jg.c.prejudge_stage.in_(judged_stages)))
            if conds:
                stmt = stmt.where(or_(*conds))
        if confidence_tier:
            stmt = stmt.where(_jg_exists(spec, jg.c.conf_tier == confidence_tier))
        if human_state == "corrected":
            # 已被人工介入（改值／新增／標記誤判任一）——走 idx_attribution_tbl_mix02 partial 索引
            stmt = stmt.where(_jg_exists(spec, human_touched_cond(), include_deleted=True))
        elif human_state == "ai_only":
            stmt = stmt.where(~_jg_exists(spec, human_touched_cond(), include_deleted=True))
        elif human_state == "suggested":
            sg = T.attribution_suggestions
            stmt = stmt.where(
                exists().where(
                    and_(
                        sg.c.feedback_source_code == spec.source,
                        sg.c.source_id == tbl.c[spec.natural_key],
                    )
                )
            )
        if model:
            # 初判模型多選（當前初判維度）；任一歸因命中即列出
            stmt = stmt.where(_jg_exists(spec, jg.c.model.in_(model)))
        if bucket and "bucket" in tbl.c:
            # 進線分桶多選（conversations 專屬直欄，其餘來源無此欄忽略）
            stmt = stmt.where(tbl.c["bucket"].in_(bucket))
        if taxonomy:
            # 歸因分類多選：任意層級 code，l1/l2_code 任一 IN 命中＝子樹語義
            # （選 L1 涵蓋整域，含只判到 L2 的列；選 L2 精確到面向）
            stmt = stmt.where(
                _jg_exists(
                    spec,
                    or_(
                        jg.c.l1_code.in_(taxonomy),
                        jg.c.l2_code.in_(taxonomy),
                    ),
                )
            )
        # 表級篩選（垂直分類/日期/oid/有無外部評論）走 SSOT，避免與初判目標選取各寫一份而漂移。
        return apply_table_filters(
            spec,
            stmt,
            vertical=vertical,
            date_from=date_from,
            date_to=date_to,
            date_field=date_field,
            rec_oid=rec_oid,
            prod_oid=prod_oid,
            order_oid=order_oid,
            has_external=has_external,
        )

    # item 級排序（白名單防注入）；confidence 取該 item 各歸因最大信心（scalar 子查詢）
    _sort_map = {
        "occurred_at": tbl.c[spec.date_col],
        "go_date": tbl.c["go_date"] if "go_date" in tbl.c else tbl.c[spec.date_col],
        "score": tbl.c[spec.score_col] if spec.score_col else tbl.c[spec.date_col],
    }
    if sort_by == "confidence":
        # 該 item 各歸因最大信心的 scalar 子查詢。_paged_fanout 外層也 join attributions，若不指定關聯範圍，
        # SQLAlchemy 會把子查詢的 attributions 也 auto-correlate 掉 → 「no FROM clauses」500。
        # correlate_except(attributions)：attributions 留在子查詢 FROM，只把外層 source 表關聯進來。
        sort_expr = (
            select(func.max(T.attributions.c.conf_value))
            .where(_jg_join_cond(spec))
            .correlate_except(T.attributions)
            .scalar_subquery()
        )
    else:
        sort_expr = _sort_map.get(sort_by or "", tbl.c[spec.date_col])
    data = _paged_fanout(spec, _f, sort_expr, sort_dir, limit, offset)
    # 待審建議數（列表徽記）：本頁 id 一次查完，不做 per-row 查詢
    # ⚠️ 取 `source_id` 而非 `spec.natural_key`：列已經過 _enrich_problem 正規化，
    # 來源專屬的自然鍵欄名（rec_oid / session_oid…）在這裡已統一成 canonical 的 source_id。
    from app.core.db import notes as _notes
    from app.core.db import suggestions as _suggestions

    ids = [str(r.get("source_id") or "") for r in data["rows"]]
    counts = _suggestions.pending_counts(spec.source, ids)
    states = _judge_states(spec.source, ids)
    notes = _notes.note_counts(spec.source, ids)
    for r in data["rows"]:
        sid = str(r.get("source_id") or "")
        r["suggestion_count"] = counts.get(sid, 0)
        state, dismissed = states.get(sid, ("unjudged", 0))
        r["judge_state"] = state
        r["dismissed_count"] = dismissed
        # 備註數徽記：**沒有可見性就沒人用**——這正是 2026-08-04 退役的人工判決軸的死法
        # （6,242 條裡只有 1 個人按過那兩顆按鈕）。備註要在列表上看得到才會被使用。
        r["note_count"] = notes.get(sid, 0)
    return data


def _judge_states(source: str, ids: list[str]) -> dict[str, tuple[str, int]]:
    """每則反饋的判定狀態 → `{source_id: (judge_state, dismissed_count)}`。

    三態由**兩個 SQL 計數**決定，不是靠「回傳陣列長不長」推斷——後者只是把同一個脆弱的推斷
    從前端搬到後端，而且一旦有人改了 fan-out 的過濾就會靜默失準：

    - `judged`：有存活歸因
    - `dismissed`：判過，但歸因全被人工標記為 AI 誤判（列表要顯示得出來，否則使用者以為資料不見了）
    - `unjudged`：從未判過

    本頁 id 一次查完（與 `pending_counts` 同款批次樣式），不做 per-row 查詢。
    """
    if not ids:
        return {}
    jg = T.attributions
    live = func.count().filter(jg.c.is_deleted == sa_false())
    dead = func.count().filter(jg.c.is_deleted == sa_true())
    with T.get_engine().connect() as c:
        rows = c.execute(
            select(jg.c.source_id, live.label("live_n"), dead.label("dead_n"))
            .where(jg.c.source == source, jg.c.source_id.in_(ids))
            .group_by(jg.c.source_id)
        ).all()
    out: dict[str, tuple[str, int]] = {i: ("unjudged", 0) for i in ids}
    for sid, live_n, dead_n in rows:
        out[str(sid)] = ("judged" if live_n else "dismissed", int(dead_n or 0))
    return out

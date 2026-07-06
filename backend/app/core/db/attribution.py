"""歸因縱覽聚合（縱覽頁專用；KPI + polarity/L1-code/星等/月趨勢 + L2/L3 下鑽）。"""

from __future__ import annotations

from sqlalchemy import cast as sa_cast
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import JSONB

from app.core.db import tables as T
from app.core.db._shared import (
    _CONFIDENCE_TIERS,
    _POLARITY_LABEL_ZH,
    _jg_join_cond,
    _vertical_codes,
    _vertical_scoped_spec,
)


def attribution_overview(
    source: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    granularity: str = "month",
    product_vertical: str | list[str] | None = None,
) -> dict:
    """歸因縱覽聚合：一次取齊 KPI + 各維度分布 + 趨勢（避免前端全量 fetch 再算）。

    傾向(polarity)分布、L1 域分布、星等分布、月度時序（已判/負向）。域軸用 data.l1_domain_code。
    polarity/l1 取自 judgments.data JSON（JSONB 抽出 GROUP BY，與 list_problems 同手法）；星等取
    spec.score_col；月份用 date_col 前 7 字（YYYY-MM）。信心分層走 Python 即時聚合（資料量小）。
    source 命中 source_registry 時查該專表；source=None（縱覽全部）走 judgments 直接聚合。

    Returns:
        {total_intake, judged, attributed, by_polarity, by_l1, by_tier, by_score, trend}。
    """
    # 縱覽（source=None）帶垂直分類篩選時改走 product_reviews（見 _vertical_scoped_spec）。
    spec = _vertical_scoped_spec(source, product_vertical)
    jg = T.judgments
    cnt = func.count().label("n")
    tiers = _CONFIDENCE_TIERS
    # judgments typed 判決欄（直接 GROUP BY / FILTER，走 btree 索引）
    pol = jg.c.polarity
    l1c = jg.c.l1_code
    l1l = jg.c.l1_label
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
                # date_col 為 raw datetime 文字（'YYYY-MM-DD HH:MM' / 'YYYY-MM-DDTHH:MM'）；用可走 btree
                # 索引的 sargable 比較取代 substr（substr 打死索引＝overview 慢）。上界含當日整天：直接
                # <= date_to 會漏當日有時間的列（'…30 08:00' > '…30'），改半開 < date_to||'~'（'~'>所有分隔符）。
                if date_from:
                    stmt = stmt.where(date_col >= date_from)
                if date_to:
                    stmt = stmt.where(date_col < date_to + "~")
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
            by_tier = _by_tier(c.execute(_src(select(jg.c.conf_value.label("confidence")).select_from(j).where(jg.c.conf_value.isnot(None)))).mappings())
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
            by_tier = _by_tier(c.execute(select(jg.c.conf_value.label("confidence")).select_from(jg).where(jg.c.conf_value.isnot(None))).mappings())
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

    L2/L3 取自 judgments.data JSON，限定該 L1 域；GROUP BY code（carry label），依筆數降序。
    source 命中 source_registry 時查該專表；source=None（縱覽全部）走 judgments 直接聚合。

    Returns:
        {l1_code, l1_label, by_l2, by_l3}；by_l2/by_l3 為 [{code, label, n}]。
    """
    # 縱覽（source=None）帶垂直分類篩選時改走 product_reviews（見 _vertical_scoped_spec）。
    spec = _vertical_scoped_spec(source, product_vertical)
    jg = T.judgments
    cnt = func.count().label("n")
    l1c, l1l = jg.c.l1_code, jg.c.l1_label
    l2c, l2l = jg.c.l2_code, jg.c.l2_label
    l3c, l3l = jg.c.l3_code, jg.c.l3_label
    _v_codes = _vertical_codes(product_vertical) if (spec is not None and spec.category_col) else []

    # spec 命中：join 該表（可套 date/vertical）；source=None：judgments 直接聚合
    if spec is not None:
        tbl = spec.table
        date_col = tbl.c[spec.date_col]
        frm = tbl.outerjoin(jg, _jg_join_cond(spec))
        extra = []
        # sargable 日期比較（走 btree 索引，取代 substr）；上界半開含當日整天，見 attribution_overview 註解。
        if date_from:
            extra.append(date_col >= date_from)
        if date_to:
            extra.append(date_col < date_to + "~")
        if _v_codes:
            extra.append(sa_cast(tbl.c[spec.category_col], JSONB)["main"].astext.in_(_v_codes))
    else:
        frm = jg
        extra = []

    # 多指標（供商品內容細化表）：負向數 / 平均信心 / 自動採信數（占比與自動採信率由前端 n 換算）。
    neg = func.count().filter(jg.c.polarity == "negative").label("neg")
    avg_conf = func.avg(jg.c.conf_value).label("avg_conf")
    auto = func.count().filter(jg.c.conf_tier == "auto_accept").label("auto")

    def _level(code_col, label_col):
        """組某層（L2/L3）GROUP BY：限定 L1 域 + 非空 code + 篩選，依筆數降序；帶多指標。"""
        stmt = (
            select(code_col.label("code"), label_col.label("label"), cnt, neg, avg_conf, auto)
            .select_from(frm)
            .where(l1c == l1, code_col.isnot(None), code_col != "")
        )
        for w in extra:
            stmt = stmt.where(w)
        return stmt.group_by(code_col, label_col).order_by(cnt.desc())

    def _rows(c, stmt):
        """執行並將 avg_conf 四捨五入（float，避免前端顯示長尾）。"""
        out = []
        for r in c.execute(stmt).mappings():
            d = dict(r)
            d["avg_conf"] = round(float(d["avg_conf"]), 3) if d["avg_conf"] is not None else None
            out.append(d)
        return out

    with T.get_engine().connect() as c:
        l1_label = (
            c.execute(select(l1l).select_from(frm).where(l1c == l1, l1l.isnot(None)).limit(1)).scalar()
            or l1
        )
        by_l2 = _rows(c, _level(l2c, l2l))
        by_l3 = _rows(c, _level(l3c, l3l))
    return {"l1_code": l1, "l1_label": l1_label, "by_l2": by_l2, "by_l3": by_l3}

"""歸因概覽聚合（概覽頁專用；KPI + polarity/L1-code/星等/月趨勢 + L2/L3 下鑽）。"""

from __future__ import annotations

from sqlalchemy import func, or_, select

from app.core.db import tables as T
from app.core.db._shared import (
    _CONFIDENCE_TIERS,
    _POLARITY_LABEL_ZH,
    _jg_join_cond,
    _vertical_codes,
    _vertical_scoped_spec,
)
from app.core.db._shared import (
    live_attr_cond as _live_attr_cond,
)

# 概覽頁「內容類占比」KPI 的分子域機器值（＝content 域，見其 prompt `## Taxonomy` root）。具名常數＋此註解，
# 取代散落 SQL 的字面量 "content"——域機器值曾改名（product_quality→quality 等），寫死易漏改而靜默失準。
_HEADLINE_DOMAIN = "content"


def attribution_overview(
    source: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    granularity: str = "month",
    vertical: str | list[str] | None = None,
    model: list[str] | None = None,
) -> dict:
    """歸因概覽聚合：一次取齊 KPI + 各維度分布 + 趨勢（避免前端全量 fetch 再算）。

    傾向(polarity)分布、L1 域分布、星等分布、月度時序（已初判/負向）。域軸用 data.l1_domain_code。
    polarity/l1 取自 attributions.data JSON（JSONB 抽出 GROUP BY，與 list_problems 同手法）；星等取
    spec.score_col；月份用 date_col 前 7 字（YYYY-MM）。信心分層走 Python 即時聚合（資料量小）。
    source 命中 source_registry 時查該專表；source=None（縱覽全部）走 attributions 直接聚合。

    model：初判模型多選（attributions.model IN——**當前初判**維度；歷史快照級聚合另計）。
    僅套用於初判級指標（judged/attributed/分布/趨勢），total_intake/by_score 為進線/星等
    語義不受影響——套用後 judged 語義變「所選模型的初判覆蓋數」，與 total_intake 差額含
    「他模型判過」非皆未初判（前端 KPI 文案需揭露）。

    Returns:
        {total_intake, judged, attributed, by_polarity, by_l1, by_tier, by_score, trend}。
    """
    # 縱覽（source=None）帶垂直分類篩選時改走 conversations（見 _vertical_scoped_spec）。
    spec = _vertical_scoped_spec(source, vertical)
    jg = T.attributions
    cnt = func.count().label("n")
    tiers = _CONFIDENCE_TIERS
    # attributions typed 初判欄（直接 GROUP BY / FILTER，走 btree 索引）
    pol = jg.c.polarity
    l1c = jg.c.l1_code
    l1l = jg.c.l1_label
    _GRAN_LEN = {"year": 4, "month": 7, "day": 10}
    glen = _GRAN_LEN.get(granularity, 7)
    _v_codes = _vertical_codes(vertical) if (spec is not None and spec.bd_tag_col) else []
    _ALL_TABLES = (
        T.reviews,
        T.conversations,
        T.freshdesk_tickets,
        T.app_feedback,
        T.mixpanel_tracker,
    )

    def _by_tier(conf_rows) -> dict:
        """信心分層分佈。**人工列不做數值分箱，直接進 human 桶。**

        人工糾正會把 `conf_value` 設為 NULL（原 AI 信心描述的是舊分類，掛在新分類上是謊言），
        只留 `conf_tier='human'`。若這裡仍照數值分箱、查詢又過濾 `conf_value IS NOT NULL`，
        人工列會從分佈裡整批消失 → `sum(by_tier) != attributed`，儀表板三桶對不上總數，
        而且**不會有任何錯誤**——只是數字悄悄變小。

        ⚠️ 2026-08-07 修：`db/corrections.py` 的寫入紀律 docstring 一直宣稱「用字面值則既有機制
        照舊運作（含 by_tier 聚合）」，但當時本函式與其查詢端都不認識 `conf_tier`，那個保證是假的。
        dev 尚無人工列所以完全隱形。現在改由 `conf_tier` 優先判定，數值分箱只作為 AI 列的兜底。
        """
        bt = {"auto_accept": 0, "jury": 0, "needs_review": 0, "human": 0}
        for r in conf_rows:
            if r.get("tier") == "human":
                bt["human"] += 1
                continue
            conf = r["confidence"]
            if conf is None:  # 非人工卻無信心值＝資料異常，歸入待複審而非靜默丟棄
                bt["needs_review"] += 1
                continue
            bt[
                "auto_accept"
                if conf >= tiers["auto_accept"]
                else ("jury" if conf >= tiers["jury_low"] else "needs_review")
            ] += 1
        return bt

    def _jgm(stmt):
        """套初判模型篩選 + 排除人工誤判 tombstone（僅初判級 query 呼叫；進線/星等 query 勿套）。

        縱覽（source=None）分支直接 `select_from(jg)` 不經 `_jg_join_cond`，故 tombstone 過濾
        必須在此補上——這是 `_shared` 兩個 chokepoint 之外需顯式處理的 4 處之一。
        （單一來源分支的 join 已由 `_jg_join_cond` 自動過濾，此處重複套用同一述詞無害。）
        """
        stmt = stmt.where(_live_attr_cond())
        return stmt.where(jg.c.model.in_(model)) if model else stmt

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
                    stmt = stmt.where(tbl.c[spec.bd_tag_col].in_(_v_codes))
                return stmt

            total_intake = c.execute(_src(select(cnt).select_from(tbl))).scalar() or 0
            judged = (
                c.execute(
                    _jgm(_src(select(cnt).select_from(j).where(jg.c.attribution_oid.isnot(None))))
                ).scalar()
                or 0
            )
            attributed = (
                c.execute(
                    _jgm(_src(select(cnt).select_from(j).where(l1c.isnot(None), l1c != "")))
                ).scalar()
                or 0
            )
            by_polarity_raw = (
                c.execute(
                    _jgm(
                        _src(
                            select(pol.label("k"), cnt)
                            .select_from(j)
                            .where(jg.c.attribution_oid.isnot(None))
                            .group_by(pol)
                            .order_by(cnt.desc())
                        )
                    )
                )
                .mappings()
                .all()
            )
            by_l1_raw = (
                c.execute(
                    _jgm(
                        _src(
                            select(l1c.label("code"), l1l.label("label"), cnt)
                            .select_from(j)
                            .where(l1c.isnot(None), l1c != "")
                            .group_by(l1c, l1l)
                            .order_by(cnt.desc())
                        )
                    )
                )
                .mappings()
                .all()
            )
            by_score_raw = (
                c.execute(
                    _src(
                        select(score_col.label("score"), cnt)
                        .select_from(tbl)
                        .where(score_col.isnot(None))
                        .group_by(score_col)
                        .order_by(score_col.asc())
                    )
                )
                .mappings()
                .all()
                if score_col is not None
                else []
            )
            by_tier = _by_tier(
                c.execute(
                    _jgm(
                        _src(
                            # 同時取 conf_tier：人工列 conf_value 為 NULL，靠 tier 才分得出來
                            select(
                                jg.c.conf_value.label("confidence"),
                                jg.c.conf_tier.label("tier"),
                            )
                            .select_from(j)
                            .where(
                                or_(
                                    jg.c.conf_value.isnot(None),
                                    jg.c.conf_tier == "human",
                                )
                            )
                        )
                    )
                ).mappings()
            )
            # substr 僅用於「月/日分組 label」（GROUP BY 顯示分組，非 WHERE 過濾）——過濾已全走上方
            # sargable 比較（見 _src），此處 substr 不影響索引使用，勿誤判為效能 bug 改寫。
            ym = func.substr(date_col, 1, glen).label("ym")
            trend_rows = (
                c.execute(
                    _jgm(
                        _src(
                            select(
                                ym,
                                func.count(jg.c.attribution_oid).label("judged"),
                                func.count().filter(pol == "negative").label("negative"),
                            )
                            .select_from(j)
                            .where(
                                date_col.isnot(None),
                                date_col != "",
                                jg.c.attribution_oid.isnot(None),
                            )
                            .group_by(ym)
                            .order_by(ym.asc())
                        )
                    )
                )
                .mappings()
                .all()
            )
        else:
            # 縱覽（source=None，無 vertical）：attributions 直接聚合（含全 5 來源）；total_intake=5 表和；無 date/星等/趨勢
            total_intake = sum(
                (c.execute(select(func.count()).select_from(t)).scalar() or 0) for t in _ALL_TABLES
            )
            judged = c.execute(_jgm(select(cnt).select_from(jg))).scalar() or 0
            attributed = (
                c.execute(
                    _jgm(select(cnt).select_from(jg).where(l1c.isnot(None), l1c != ""))
                ).scalar()
                or 0
            )
            by_polarity_raw = (
                c.execute(
                    _jgm(
                        select(pol.label("k"), cnt)
                        .select_from(jg)
                        .group_by(pol)
                        .order_by(cnt.desc())
                    )
                )
                .mappings()
                .all()
            )
            by_l1_raw = (
                c.execute(
                    _jgm(
                        select(l1c.label("code"), l1l.label("label"), cnt)
                        .select_from(jg)
                        .where(l1c.isnot(None), l1c != "")
                        .group_by(l1c, l1l)
                        .order_by(cnt.desc())
                    )
                )
                .mappings()
                .all()
            )
            by_score_raw = []
            by_tier = _by_tier(
                c.execute(
                    _jgm(
                        # 同時取 conf_tier：人工列 conf_value 為 NULL，靠 tier 才分得出來
                        select(
                            jg.c.conf_value.label("confidence"),
                            jg.c.conf_tier.label("tier"),
                        )
                        .select_from(jg)
                        .where(
                            or_(
                                jg.c.conf_value.isnot(None),
                                jg.c.conf_tier == "human",
                            )
                        )
                    )
                ).mappings()
            )
            trend_rows = []

    by_polarity = [
        {
            "polarity": r["k"] or "unjudged",  # NULL＝未初判（尚未進初判管線，非中立）
            "label": _POLARITY_LABEL_ZH.get(r["k"], r["k"] or "未初判"),
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
    vertical: str | list[str] | None = None,
    model: list[str] | None = None,
) -> dict:
    """某 L1 歸因域下的 L2 面向分布（縱覽下鑽·懶載）。

    L2 取自 attributions typed 欄，限定該 L1 域；GROUP BY code（carry label），依筆數降序。
    source 命中 source_registry 時查該專表；source=None（縱覽全部）走 attributions 直接聚合。
    model：初判模型多選（attributions.model IN，當前初判維度；與 attribution_overview 同口徑）。

    Returns:
        {l1_code, l1_label, by_l2}；by_l2 為 [{code, label, n, neg, avg_conf, auto}]。
    """
    # 縱覽（source=None）帶垂直分類篩選時改走 conversations（見 _vertical_scoped_spec）。
    spec = _vertical_scoped_spec(source, vertical)
    jg = T.attributions
    cnt = func.count().label("n")
    l1c, l1l = jg.c.l1_code, jg.c.l1_label
    l2c, l2l = jg.c.l2_code, jg.c.l2_label
    _v_codes = _vertical_codes(vertical) if (spec is not None and spec.bd_tag_col) else []

    # spec 命中：join 該表（可套 date/vertical）；source=None：attributions 直接聚合
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
            extra.append(tbl.c[spec.bd_tag_col].in_(_v_codes))
    else:
        frm = jg
        extra = []
    if model:
        # 初判模型篩選：進 extra 統一由 _level() 套用
        extra.append(jg.c.model.in_(model))

    # 多指標（供商品內容細化表）：負向數 / 平均信心 / 自動採信數（占比與自動採信率由前端 n 換算）。
    neg = func.count().filter(jg.c.polarity == "negative").label("neg")
    avg_conf = func.avg(jg.c.conf_value).label("avg_conf")
    auto = func.count().filter(jg.c.conf_tier == "auto_accept").label("auto")

    def _level(code_col, label_col):
        """組 L2 GROUP BY：限定 L1 域 + 非空 code + 篩選，依筆數降序；帶多指標。"""
        cols = [code_col.label("code"), label_col.label("label"), cnt, neg, avg_conf, auto]
        stmt = select(*cols).select_from(frm).where(l1c == l1, code_col.isnot(None), code_col != "")
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
            c.execute(
                select(l1l).select_from(frm).where(l1c == l1, l1l.isnot(None)).limit(1)
            ).scalar()
            or l1
        )
        by_l2 = _rows(c, _level(l2c, l2l))
    return {"l1_code": l1, "l1_label": l1_label, "by_l2": by_l2}


def ai_judge_overview_stats(months: int = 6) -> dict:
    """AI 法官真實指標（overview 首頁「縮窄真接」範圍）：內容類占比月趨勢 + 總量。

    口徑（誠實標註）：以 attributions.created_at 初判時間分組（非來源進線時間——跨 5 來源的
    進線時間欄各異，統一軸以初判時間為準）；一則進線＝distinct (source, source_id)：
    - ratio ＝ 該月「含 content 主因歸因的進線數」/「該月已初判進線數」（1:N 多歸因不重複計）。
    - substr 用於月分組 label（顯示分組非過濾，同 attribution_overview 趨勢註解）。

    **tombstone 的兩種口徑（2026-08-07 收斂，不要「順手統一」）**：
    - 分母 `judged`／`judged_items`＝**ever**（含人工標記為 AI 誤判的列）——問的是「判過沒有」，
      必須與 `list_problems(judged=…)` 與 `prejudge_targets` 一致，否則同一件事三個畫面三個數字。
    - 分子 `content`／`content_items` 與 `attributed_rows`＝**live**（排除 tombstone）——
      問的是「現在有哪些歸因」，人說判錯的不該再算數。
    → 一則歸因全被標記誤判的反饋：計入分母、不計入分子，ratio 因此下降。這是正確的。

    Args:
        months: 回傳最近 N 個月（trend/spark 消費端固定 6 點）。

    Returns:
        {monthly: [{ym, judged, content, ratio_pct}], totals: {judged_items, attributed_rows,
         content_items, content_share_pct}}；空庫時 monthly=[]、totals 全 0。
    """
    from sqlalchemy import distinct

    jg = T.attributions
    item = jg.c.source + ":" + jg.c.source_id  # distinct 進線鍵（1:N 多歸因去重）
    # create_date 是 timestamptz（非 text）：用 to_char 取 YYYY-MM，勿用 substr
    ym = func.to_char(jg.c.created_at, "YYYY-MM").label("ym")
    with T.get_engine().connect() as c:
        rows = (
            c.execute(
                select(
                    ym,
                    # 分母＝已初判進線（**ever 口徑，含 tombstone**，與列表 judged / prejudge_targets
                    # 同一把尺：人工標記為 AI 誤判代表「判過但判錯」，仍然是判過）
                    func.count(distinct(item)).label("judged"),
                    # 分子＝有 content 主因的進線（**live 口徑**：被人工判定為誤判的歸因不該再算數）
                    func.count(distinct(item))
                    .filter(_live_attr_cond(), jg.c.l1_code == _HEADLINE_DOMAIN)
                    .label("content"),
                )
                .where(jg.c.created_at.isnot(None))
                .group_by(ym)
                .order_by(ym.asc())
            )
            .mappings()
            .all()
        )
        judged_items = (
            # 「已初判進線」＝**ever 口徑**（不套 live_attr_cond）。前端 label 就叫「已初判進線」，
            # 與列表的「已初判」是同一個問題，必須同一把尺——用 live 口徑的話，歸因全被標記誤判的
            # 反饋在概覽會消失、在列表卻還在，兩個畫面對同一件事給出不同數字。
            c.execute(select(func.count(distinct(item)))).scalar() or 0
        )
        attributed_rows = (
            c.execute(
                select(func.count())
                .select_from(jg)
                # tombstone 過濾（本 query 不經 _jg_join_cond / _jgm，需顯式補）
                .where(_live_attr_cond(), jg.c.l1_code.isnot(None), jg.c.l1_code != "")
            ).scalar()
            or 0
        )
        content_items = (
            c.execute(
                select(func.count(distinct(item))).where(
                    # tombstone 過濾（同 attributed_rows：本 query 不經 _jg_join_cond / _jgm，需顯式補）。
                    # 少了這條，content_share_pct 會變成「含 tombstone ÷ 不含 tombstone」——
                    # 分子分母不同口徑，百分比會虛高且不報錯（2026-08-07 實際發生過）。
                    _live_attr_cond(),
                    jg.c.l1_code == _HEADLINE_DOMAIN,
                )
            ).scalar()
            or 0
        )
    monthly = [
        {
            "ym": r["ym"],
            "judged": int(r["judged"]),
            "content": int(r["content"]),
            "ratio_pct": round(r["content"] / r["judged"] * 100, 2) if r["judged"] else 0.0,
        }
        for r in rows[-months:]
    ]
    return {
        "monthly": monthly,
        "totals": {
            "judged_items": int(judged_items),
            "attributed_rows": int(attributed_rows),
            "content_items": int(content_items),
            "content_share_pct": round(content_items / judged_items * 100, 2)
            if judged_items
            else 0.0,
        },
    }

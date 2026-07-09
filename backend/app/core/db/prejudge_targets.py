"""初判 / 再判「目標選取」：解析批量判決的標的特徵 id 清單（stage 驅動 + 列表全維度篩選）。"""

from __future__ import annotations

from sqlalchemy import or_, select

from app.core.db import source_registry
from app.core.db import tables as T
from app.core.db._shared import _jg_join_cond, apply_table_filters


def prejudge_target_ids(
    source: str | None = None,
    product_vertical: str | list[str] | None = None,
    stages: list[str] | None = None,
    target_polarity: list[str] | None = None,
    max_confidence: float | None = None,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    date_field: str = "occurred_at",
    rec_oid: str | None = None,
    prod_oid: str | None = None,
    order_oid: str | None = None,
    confidence_tier: str | None = None,
    taxonomy: list[str] | None = None,
    has_external: bool | None = None,
    within_ids: list[str] | None = None,
) -> list[str]:
    """初判歸因「目標選取」的特徵 id 清單（scope=all；stage 驅動 + 列表全維度篩選）。

    只 SELECT 特徵 id、不跑 _enrich_problem，避免對全量做 source_mapping 還原的無謂開銷；
    供 prejudge_batch 批量派工前一次解析標的集合。source 命中 source_registry 查該專表；
    source=None（縱覽全部）無單表可查故回空清單。

    選取邏輯（兩分支聯集去重）：
    - stages 含 'unjudged' → 收「無 finding 列」特徵 id（首判）。
    - stages 含已判階段（judged/pending_review/pending_data/insufficient）→ 收
      judgments.stage ∈ 該些階段，並可再收斂 target_polarity / max_confidence /
      confidence_tier / taxonomy（只重判負向低信心等場景，避免浪費 token 重判已確定者）。
    - 表級篩選（垂直分類/日期區間/關聯 oid/有無外部評論）兩分支皆套，語義與統一問題列表
      同一份 SSOT（_shared.apply_table_filters）——「歸因目標＝列表當前篩得到的東西」。
      判決級收斂（傾向/信心/歸因分類）只對已判分支有意義（未判列無判決可比對）。

    Args:
        source: 來源 code（None＝全部來源，回空；5 來源全拆表須指定單一來源）。
        product_vertical: 商品垂直分類分組（僅 spec.category_col 存在的來源生效）。
        stages: 目標判決階段清單（預設 ["unjudged"]）。
        target_polarity: 已判分支的傾向收斂（多選 IN，如 ["negative"]；None/空＝不收斂）。
        max_confidence: 已判分支的信心上限（confidence < 此值才收；None＝不收斂）。
        date_from/date_to: 日期區間（'YYYY-MM-DD'，含端點）。
        date_field: 日期篩選欄名（'occurred_at' | 'go_date'）。
        rec_oid/prod_oid/order_oid: 關聯資料精確篩選（表有對應欄才生效）。
        confidence_tier: 已判分支的信心分層收斂（auto_accept/jury/needs_review）。
        taxonomy: 已判分支的歸因分類收斂（任意層級 code 多選；l1/l2/l3_code 任一 IN 命中＝子樹語義）。
        has_external: 有無外部評論融合資料（表級，兩分支皆套；僅 product_reviews 生效）。
        within_ids: 範圍收斂——僅在此特徵 id 清單內做目標選取（前端「已選 N 筆內」；
            兩分支皆套；None＝不限、空清單＝空範圍回空）。

    Returns:
        目標特徵 id 清單（去重；順序不保證）。
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
        """表級篩選（與列表共用 SSOT）：垂直分類/日期區間/關聯 oid + 勾選範圍收斂。"""
        stmt = apply_table_filters(
            spec,
            stmt,
            product_vertical=product_vertical,
            date_from=date_from,
            date_to=date_to,
            date_field=date_field,
            rec_oid=rec_oid,
            prod_oid=prod_oid,
            order_oid=order_oid,
            has_external=has_external,
        )
        if within_ids is not None:  # 空清單＝空範圍（IN () 恆假），非「不限」
            stmt = stmt.where(nk.in_(within_ids))
        return stmt

    ids: set[str] = set()
    with T.get_engine().connect() as c:
        if want_unjudged:
            s = _scope(select(nk).select_from(j).where(jg.c.finding_id.is_(None)))
            ids.update(r[0] for r in c.execute(s))
        if judged_stages:
            s = select(nk).select_from(j).where(jg.c.finding_id.isnot(None))
            s = s.where(jg.c.stage.in_(judged_stages))
            if target_polarity:
                s = s.where(jg.c.polarity.in_(target_polarity))
            if max_confidence is not None:
                s = s.where(jg.c.conf_value < max_confidence)
            if confidence_tier:
                s = s.where(jg.c.conf_tier == confidence_tier)
            if taxonomy:
                # 歸因分類多選：任意層級 code 命中（子樹語義，同列表篩選）
                s = s.where(
                    or_(
                        jg.c.l1_code.in_(taxonomy),
                        jg.c.l2_code.in_(taxonomy),
                        jg.c.l3_code.in_(taxonomy),
                    )
                )
            ids.update(r[0] for r in c.execute(_scope(s)))
    return [str(x) for x in ids if x is not None]

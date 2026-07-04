"""初判 / 再判「目標選取」：解析批量判決的標的特徵 id 清單（stage 驅動）。"""

from __future__ import annotations

from sqlalchemy import cast as sa_cast
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB

from app.core.db import source_registry
from app.core.db import tables as T
from app.core.db._shared import _jg_join_cond, _vertical_codes


def prejudge_target_ids(
    source: str | None = None,
    product_vertical: str | list[str] | None = None,
    stages: list[str] | None = None,
    target_polarity: str | None = None,
    max_confidence: float | None = None,
) -> list[str]:
    """初判歸因「目標選取」的特徵 id 清單（scope=all；stage 驅動）。

    只 SELECT 特徵 id、不跑 _enrich_problem，避免對全量做 source_mapping 還原的無謂開銷；
    供 prejudge_batch 批量派工前一次解析標的集合。source 命中 source_registry 查該專表；
    source=None（縱覽全部）無單表可查故回空清單。

    選取邏輯（兩分支聯集去重）：
    - stages 含 'unjudged' → 收「無 finding 列」特徵 id（首判）。
    - stages 含已判階段（judged/pending_review/pending_data/insufficient）→ 收
      judgments.data.judgment_stage ∈ 該些階段，並可再收斂 target_polarity 與 max_confidence
      （只重判已判中負向且低信心等場景，避免浪費 token 重判已確定的正向/高信心）。

    Args:
        source: 來源 code（None＝全部來源，回空；5 來源全拆表須指定單一來源）。
        product_vertical: 商品垂直分類分組（僅 spec.category_col 存在的來源生效）。
        stages: 目標判決階段清單（預設 ["unjudged"]）。
        target_polarity: 已判分支的傾向收斂（如 "negative"；None＝不收斂）。
        max_confidence: 已判分支的信心上限（confidence < 此值才收；None＝不收斂）。

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

"""db 子模組共用：judgment.json 顯示標籤 / 信心閾值 + 複合鍵 join + 商品垂直分類 + 時間格式化。

problems / prejudge_targets / attribution / export 多處共用，抽出為單一真相（Rule of Three）。
判決顯示 label + 信心閾值 SSOT＝config/ai_judge/judgment.json（前後端同讀）；db 不能 import settings
（settings 已 import db → 循環），故以 paths.AI_JUDGE_DIR 自讀該檔。
"""

from __future__ import annotations

import json
import re

from sqlalchemy import Float, and_, exists
from sqlalchemy import cast as sa_cast
from sqlalchemy.dialects.postgresql import JSONB

from app.core.db import source_registry
from app.core.db import tables as T
from app.core.paths import AI_JUDGE_DIR as _AI_JUDGE_DIR

# ── 判決顯示標籤 + 信心閾值（judgment.json；取代已移除的 taxonomy）───────────────
try:
    _JUDGMENT_CFG: dict = json.loads((_AI_JUDGE_DIR / "judgment.json").read_text(encoding="utf-8"))
except (OSError, ValueError):
    _JUDGMENT_CFG = {}

_POLARITY_LABEL_ZH: dict[str, str] = _JUDGMENT_CFG.get("polarity_labels", {})
_TIER_LABEL_ZH: dict[str, str] = _JUDGMENT_CFG.get("tier_labels", {})
_STAGE_LABEL_ZH: dict[str, str] = _JUDGMENT_CFG.get("stage_labels", {})
_CONFIDENCE_TIERS: dict = _JUDGMENT_CFG.get(
    "confidence_tiers", {"auto_accept": 0.8, "jury_low": 0.5, "jury_high": 0.7}
)


# ── judgments.data 分組物件的存取 SSOT（形狀＝schema.TicketFinding.to_stored；改形狀只改此處）──
# data 攤平重整後為乾淨分組物件：polarity / stage / attribution{l1,l2,l3{code,label}}
# / confidence{value,raw,tier} / content{summary,evidence,action} / meta{model,primary,judgedAt}。
# 查詢層（GROUP BY / FILTER / SORT）用下列 d_* 抽欄 expression；Python 層（json.loads 後）用 read_stored。


def _jdata():
    """judgments.data(Text) → JSONB（查詢層抽欄基底）。"""
    return sa_cast(T.judgments.c.data, JSONB)


def d_polarity():
    """data.polarity 抽為 text expression（GROUP BY / 篩選用）。"""
    return _jdata()["polarity"].astext


def d_stage():
    """data.stage 抽為 text expression。"""
    return _jdata()["stage"].astext


def d_tier():
    """data.confidence.tier 抽為 text expression。"""
    return _jdata()["confidence"]["tier"].astext


def d_conf_value():
    """data.confidence.value 抽為 Float expression（排序 / 聚合用；取代舊 confidence scalar 欄）。"""
    return sa_cast(_jdata()["confidence"]["value"].astext, Float)


def d_l1_code():
    """data.attribution.l1.code 抽為 text expression。"""
    return _jdata()["attribution"]["l1"]["code"].astext


def d_l1_label():
    """data.attribution.l1.label 抽為 text expression。"""
    return _jdata()["attribution"]["l1"]["label"].astext


def d_l2_code():
    """data.attribution.l2.code 抽為 text expression。"""
    return _jdata()["attribution"]["l2"]["code"].astext


def d_l2_label():
    """data.attribution.l2.label 抽為 text expression。"""
    return _jdata()["attribution"]["l2"]["label"].astext


def d_l3_code():
    """data.attribution.l3.code 抽為 text expression。"""
    return _jdata()["attribution"]["l3"]["code"].astext


def d_l3_label():
    """data.attribution.l3.label 抽為 text expression。"""
    return _jdata()["attribution"]["l3"]["label"].astext


def read_stored(data: dict) -> dict:
    """judgments.data 分組物件 → 扁平顯示 dict（Python 層還原 SSOT）。

    read adapter：對齊攤平前的舊扁平 key（l1_domain_code/l1_label/confidence_tier/
    judgment_stage/problem_summary/evidence_quote/recommended_action…），故 _attribution_of /
    _enrich_problem / export / accuracy 讀新分組 data 但輸出鍵不變 → 前端與導出零改動。

    Args:
        data: json.loads(judgments.data) 後的分組物件（見 schema.TicketFinding.to_stored）。

    Returns:
        扁平 dict（含 confidence_value / raw_confidence / is_primary / judged_at 等）。
    """
    attr = data.get("attribution") or {}
    l1 = attr.get("l1") or {}
    l2 = attr.get("l2") or {}
    l3 = attr.get("l3") or {}
    conf = data.get("confidence") or {}
    content = data.get("content") or {}
    meta = data.get("meta") or {}
    return {
        "polarity": data.get("polarity"),
        "judgment_stage": data.get("stage"),
        "l1_domain_code": l1.get("code"),
        "l1_label": l1.get("label"),
        "l2_code": l2.get("code"),
        "l2_label": l2.get("label"),
        "l3_code": l3.get("code"),
        "l3_label": l3.get("label"),
        "confidence_value": conf.get("value"),
        "raw_confidence": conf.get("raw"),
        "confidence_tier": conf.get("tier"),
        "problem_summary": content.get("summary"),
        "evidence_quote": content.get("evidence"),
        "recommended_action": content.get("action"),
        "model_used": meta.get("model"),
        "is_primary": meta.get("primary"),
        "judged_at": meta.get("judgedAt"),
    }


def _jg_join_cond(spec):
    """judgments 與來源表的複合鍵 join 條件：source + source_id == 該表特徵 id 欄。"""
    jg = T.judgments
    return and_(jg.c.source == spec.source, jg.c.source_id == spec.table.c[spec.natural_key])


def _jg_exists(spec, *extra):
    """`EXISTS (SELECT 1 FROM judgments WHERE source=X AND source_id=特徵id [AND ...])`。"""
    return exists().where(and_(_jg_join_cond(spec), *extra))


def _vertical_codes(product_vertical: str | list[str] | None) -> list[str]:
    """商品垂直分類分組名 → CATEGORY 代碼清單（多分組 extend 合併；空/None 回空清單）。

    局部 import：product_vertical loader 讀 db.get_rule_active → 頂層 import 會造成循環依賴。
    供 list_problems / overview / breakdown / prejudge_targets 共用（比對 spec.category_col 的 IN 篩選）。
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

    有篩選時只統計「有分類且落在所選分類」的資料，無分類來源（進線/工單）在有篩選時排除。
    無篩選則回 None，呼叫端走 judgments 直接聚合維持「全部來源」語義。
    """
    spec = source_registry.spec_for(source)
    if spec is None and _vertical_codes(product_vertical):
        spec = source_registry.spec_for("product_reviews")
    return spec


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

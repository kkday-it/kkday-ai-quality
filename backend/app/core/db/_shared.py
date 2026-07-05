"""db 子模組共用：judgment.json 顯示標籤 / 信心閾值 + 複合鍵 join + 商品垂直分類 + 時間格式化。

problems / prejudge_targets / attribution / export 多處共用，抽出為單一真相（Rule of Three）。
判決顯示 label + 信心閾值 SSOT＝config/ai_judge/judgment.json（前後端同讀）；db 不能 import settings
（settings 已 import db → 循環），故以 paths.AI_JUDGE_DIR 自讀該檔。
"""

from __future__ import annotations

import json
import re

from sqlalchemy import and_, exists

from app.core.db import source_registry
from app.core.db import tables as T
from app.core.paths import AI_JUDGE_DIR as _AI_JUDGE_DIR

# ── 判決顯示標籤 + 信心閾值（judgment.json；取代已移除的 taxonomy）───────────────
# 皆為 module 級 dict，熱重載時就地 clear+update（不重綁），使既有 import 引用（attribution/export）
# 同步反映新值、無需改呼叫端。SSOT＝DB active 'judgment' 版（規則管理可熱更新），缺版本回退 seed 檔。
_DEFAULT_TIERS: dict = {"auto_accept": 0.8, "jury_low": 0.5, "jury_high": 0.7}
_POLARITY_LABEL_ZH: dict[str, str] = {}
_TIER_LABEL_ZH: dict[str, str] = {}
_STAGE_LABEL_ZH: dict[str, str] = {}
_CONFIDENCE_TIERS: dict = {}


def _apply_judgment_cfg(cfg: dict) -> None:
    """將 judgment 配置就地灌入 module 級 label / 閾值 dict（clear+update 保持同一物件引用）。"""
    _POLARITY_LABEL_ZH.clear()
    _POLARITY_LABEL_ZH.update(cfg.get("polarity_labels", {}))
    _TIER_LABEL_ZH.clear()
    _TIER_LABEL_ZH.update(cfg.get("tier_labels", {}))
    _STAGE_LABEL_ZH.clear()
    _STAGE_LABEL_ZH.update(cfg.get("stage_labels", {}))
    _CONFIDENCE_TIERS.clear()
    _CONFIDENCE_TIERS.update(cfg.get("confidence_tiers", _DEFAULT_TIERS))


def _read_judgment_file() -> dict:
    """讀 seed 檔 config/ai_judge/judgment.json（import 期安全來源；DB 引擎未必就緒時用）。"""
    try:
        return json.loads((_AI_JUDGE_DIR / "judgment.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def reload_judgment_cfg() -> None:
    """熱重載 judgment 配置（規則管理存檔後由 rules._reload_judge_cache 呼叫，對齊 ai_judge/global_rule）。

    SSOT＝DB active 版（`rule_versions.get_rule_active('judgment')`），缺版本 / DB 未就緒回退 seed 檔；
    就地更新 label / 閾值 dict，使 import 引用免改碼即反映新值。
    """
    from app.core.db import rule_versions as _rv

    cfg: dict | None = None
    try:
        cfg = _rv.get_rule_active("judgment")
    except Exception:  # noqa: BLE001  DB 未就緒 / 查詢失敗 → 回退 seed 檔，不阻斷
        cfg = None
    _apply_judgment_cfg(cfg if cfg is not None else _read_judgment_file())


# import 期以 seed 檔初始化（DB 引擎未必就緒；DB active 熱更新由 reload_judgment_cfg 於 runtime 觸發）。
_apply_judgment_cfg(_read_judgment_file())


# ── 判決 API DTO：judgments typed 欄 → 乾淨巢狀物件（storage=typed 欄；呈現=巢狀 DTO 的 SSOT）──
# 一條形狀貫穿 DB→API→前端：DB 存 typed 欄（可 btree 索引 / 乾淨 SQL），此處組成前端消費的
# 巢狀 DTO。改 DTO 形狀只改此處（前端 Attribution interface 對齊）。


def attribution_dto(r: dict) -> dict:
    """judgments 列（typed 欄 mapping）→ 一條歸因的乾淨巢狀 DTO（API/前端 SSOT）。

    r 為含判決欄的 mapping（fan-out 走 jg_ 前綴 → 呼叫端先 unwrap 成無前綴 dict 再傳入，
    或直接傳 judgments 列 mapping）。人工覆核軸（status/true_label）與 finding_id 亦在其中。

    Args:
        r: 判決欄 mapping（finding_id/polarity/stage/l1_code…/conf_value/summary/status…）。

    Returns:
        巢狀 DTO：{finding_id, polarity, stage, l1/l2/l3:{code,label},
        confidence:{value,raw,tier}, content:{summary,evidence,action},
        is_primary, status, true_label}。
    """
    return {
        "finding_id": r.get("finding_id"),
        "polarity": r.get("polarity"),
        "stage": r.get("stage"),
        "l1": {"code": r.get("l1_code"), "label": r.get("l1_label")},
        "l2": {"code": r.get("l2_code"), "label": r.get("l2_label")},
        "l3": {"code": r.get("l3_code"), "label": r.get("l3_label")},
        "confidence": {"value": r.get("conf_value"), "raw": r.get("conf_raw"), "tier": r.get("conf_tier")},
        "content": {"summary": r.get("summary"), "evidence": r.get("evidence"), "action": r.get("action")},
        "is_primary": r.get("is_primary"),
        "status": r.get("status"),  # 人工覆核狀態（覆核徽章用）
        "true_label": r.get("true_label"),  # 人工標註真值分類
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

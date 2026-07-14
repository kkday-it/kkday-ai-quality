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
from app.core.judge_config.ai_judge import domain_owner as _domain_owner
from app.core.paths import AI_JUDGE_DIR as _AI_JUDGE_DIR

# ── 判決顯示標籤 + 信心閾值（judgment.json；取代已移除的 taxonomy）───────────────
# 皆為 module 級 dict，熱重載時就地 clear+update（不重綁），使既有 import 引用（attribution/export）
# 同步反映新值、無需改呼叫端。SSOT＝DB active 'judgment' 版（規則管理可熱更新），缺版本回退 seed 檔。
_DEFAULT_TIERS: dict = {"auto_accept": 0.8, "jury_low": 0.5, "jury_high": 0.7}
# status_labels code-side fallback：judgment SSOT＝DB active 版（可熱編輯），舊 active 版尚無
# status_labels 鍵時仍需有 label（seed 檔同步有此鍵，QC 存新版後以 DB 為準）。
_DEFAULT_STATUS_LABELS: dict[str, str] = {
    "new": "待處理",
    "auto_confirmed": "自動確認",
    "confirmed": "已確認",
    "dismissed": "已忽略",
}
_POLARITY_LABEL_ZH: dict[str, str] = {}
_TIER_LABEL_ZH: dict[str, str] = {}
_STAGE_LABEL_ZH: dict[str, str] = {}
_STATUS_LABEL_ZH: dict[str, str] = {}
_CONFIDENCE_TIERS: dict = {}


def _apply_judgment_cfg(cfg: dict) -> None:
    """將 judgment 配置就地灌入 module 級 label / 閾值 dict（clear+update 保持同一物件引用）。"""
    _POLARITY_LABEL_ZH.clear()
    _POLARITY_LABEL_ZH.update(cfg.get("polarity_labels", {}))
    _TIER_LABEL_ZH.clear()
    _TIER_LABEL_ZH.update(cfg.get("tier_labels", {}))
    _STAGE_LABEL_ZH.clear()
    _STAGE_LABEL_ZH.update(cfg.get("stage_labels", {}))
    _STATUS_LABEL_ZH.clear()
    _STATUS_LABEL_ZH.update(cfg.get("status_labels") or _DEFAULT_STATUS_LABELS)
    _CONFIDENCE_TIERS.clear()
    _CONFIDENCE_TIERS.update(cfg.get("confidence_tiers", _DEFAULT_TIERS))


def _read_judgment_file() -> dict:
    """讀 seed 檔 config/ai_judge/judgment.json（import 期安全來源；DB 引擎未必就緒時用）。"""
    try:
        return json.loads((_AI_JUDGE_DIR / "judgment.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def read_judgment_config() -> dict:
    """讀 judgment 判決配置（config/ai_judge/judgment.json：顯示 label + 信心閾值 + prejudge 旋鈕 +
    極性閘門 polarity_gate + 證據政策 evidence_policy）。

    2026-07-13 起 judgment 降為**專案靜態設定檔**（移出 RULE_CODES、不再 DB 版本化 / 不列規則頁）——
    直讀檔案即單一真相源，不再走 DB active。改值＝改檔 + 重啟（或 reload_judgment_cfg 熱重載）。
    同日原 global_rule.json（polarity_gate/evidence_policy）併入本檔，減少判決 config 檔案數。
    保留此函式為 judgment 讀取的**單一入口**（_shared 熱重載、prejudge 旋鈕快取共用；Rule of Three）。
    """
    return _read_judgment_file()


def reload_judgment_cfg() -> None:
    """熱重載 judgment 配置（規則管理存檔後由 rules._reload_judge_cache 呼叫，對齊 ai_judge）。

    就地更新 label / 閾值 dict（DB active 優先，見 read_judgment_config），使 import 引用免改碼即反映新值。
    """
    _apply_judgment_cfg(read_judgment_config())


# import 期以 seed 檔初始化（DB 引擎未必就緒；DB active 熱更新由 reload_judgment_cfg 於 runtime 觸發）。
_apply_judgment_cfg(_read_judgment_file())


# ── 判決 API DTO：judgments typed 欄 → 乾淨巢狀物件（storage=typed 欄；呈現=巢狀 DTO 的 SSOT）──
# 一條形狀貫穿 DB→API→前端：DB 存 typed 欄（可 btree 索引 / 乾淨 SQL），此處組成前端消費的
# 巢狀 DTO。改 DTO 形狀只改此處（前端 Attribution interface 對齊）。


def _summary_langs(raw) -> dict:
    """DB summary 值 → 語系→摘要 map。JSONB→dict；舊 JSON 字串→parse；純字串→{zh-tw:…}；None→{}。"""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        s = raw.strip()
        if s.startswith("{"):
            try:
                d = json.loads(s)
                return d if isinstance(d, dict) else {"zh-tw": s}
            except (ValueError, TypeError):
                return {"zh-tw": s}
        return {"zh-tw": s} if s else {}
    return {}


def attribution_dto(r: dict) -> dict:
    """judgments 列（typed 欄 mapping）→ 一條歸因的乾淨巢狀 DTO（API/前端 SSOT）。

    r 為含判決欄的 mapping（fan-out 走 jg_ 前綴 → 呼叫端先 unwrap 成無前綴 dict 再傳入，
    或直接傳 judgments 列 mapping）。人工覆核軸（status）與 finding_id 亦在其中。

    Args:
        r: 判決欄 mapping（finding_id/polarity/stage/l1_code…/conf_value/summary/status…）。

    Returns:
        巢狀 DTO：{finding_id, polarity, stage, l1/l2/l3:{code,label},
        confidence:{value,raw,tier}, content:{summary,evidence,action},
        owner, model, notes_count, is_primary, status}。
    """
    l1_code = r.get("l1_code")
    summary_langs = _summary_langs(r.get("summary"))
    return {
        "finding_id": r.get("finding_id"),
        "polarity": r.get("polarity"),
        "sentiment_score": r.get(
            "sentiment_score"
        ),  # 我方情緒分 1-5（與外部評論 sentiment 同尺度）
        "stage": r.get("stage"),
        "l1": {"code": l1_code, "label": r.get("l1_label")},
        "l2": {"code": r.get("l2_code"), "label": r.get("l2_label")},
        "l3": {"code": r.get("l3_code"), "label": r.get("l3_label")},
        "confidence": {
            "value": r.get("conf_value"),
            "raw": r.get("conf_raw"),
            "tier": r.get("conf_tier"),
        },
        # summary＝表格顯示用 zh-tw 字串（前端零改）；summary_langs＝全語系 map（詳情/未來多語用）
        "content": {
            "summary": summary_langs.get("zh-tw") or next(iter(summary_langs.values()), None),
            "summary_langs": summary_langs,
            "evidence": r.get("evidence"),
            "action": r.get("action"),
        },
        # 負責單位：讀取時自 l1_code 派生（SSOT＝rule _meta.owner_role；業務未填時為空字串，前端不顯示）
        "owner": _domain_owner(l1_code or ""),
        "model": r.get("model"),  # 判決模型（stub / ensemble 同 judgments.model 語意）
        "notes_count": r.get("notes_count") or 0,  # 備註數（fan-out subquery；單列讀取無值時 0）
        "is_primary": r.get("is_primary"),
        "status": r.get("status"),  # 人工覆核狀態（覆核徽章用）
    }


def _jg_join_cond(spec):
    """judgments 與來源表的複合鍵 join 條件：source + source_id == 該表特徵 id 欄。"""
    jg = T.judgments
    return and_(jg.c.source == spec.source, jg.c.source_id == spec.table.c[spec.natural_key])


def _jg_exists(spec, *extra):
    """`EXISTS (SELECT 1 FROM judgments WHERE source=X AND source_id=特徵id [AND ...])`。"""
    return exists().where(and_(_jg_join_cond(spec), *extra))


def _csv_ids(value: str) -> list[str]:
    """逗號分隔 id 字串 → 去空白去空的清單（「1, 2 ,3」→ ['1','2','3']）；單值回單元素清單。"""
    return [p.strip() for p in str(value).split(",") if p.strip()]


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


def apply_table_filters(
    spec,
    stmt,
    *,
    product_vertical: str | list[str] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    date_field: str = "occurred_at",
    rec_oid: str | None = None,
    prod_oid: str | None = None,
    order_oid: str | None = None,
    has_external: bool | None = None,
):
    """來源表級篩選 SSOT（商品垂直分類/日期區間/關聯 oid/有無外部評論）——統一問題列表與初判目標選取共用。

    僅含「來源表自身欄位」的條件；判決級條件（polarity/stage/tier/歸因分類）因兩端結構不同
    （列表用 EXISTS、目標選取用 join 分支）由各呼叫端自行套。語義逐條對齊 list_problems：
    - product_vertical：分組名經 codes_for_group 展開，product_category JSON 抽 main 比對。
    - 日期：sargable 比較走 btree 索引；上界半開 `< date_to||'~'` 含當日整天。
      date_field='go_date' 且表有 lst_dt_go 用之，否則 spec.date_col。
    - rec_oid/prod_oid/order_oid：表有對應欄才生效。
    - has_external：有無外部評論融合資料（僅有 review_external_lst_oid 欄的來源生效，如 product_reviews）。
    """
    from sqlalchemy import and_, or_
    from sqlalchemy import cast as sa_cast
    from sqlalchemy.dialects.postgresql import JSONB

    tbl = spec.table
    if spec.category_col:
        codes = _vertical_codes(product_vertical)
        if codes:
            stmt = stmt.where(sa_cast(tbl.c[spec.category_col], JSONB)["main"].astext.in_(codes))
    # rec_oid/prod_oid/order_oid：支援逗號分隔多值（「1,2,3」→ IN 一起查）；單值＝IN 單元素。
    if rec_oid and spec.natural_key in tbl.c:
        stmt = stmt.where(tbl.c[spec.natural_key].in_(_csv_ids(rec_oid)))
    if prod_oid and "prod_oid" in tbl.c:
        stmt = stmt.where(tbl.c.prod_oid.in_(_csv_ids(prod_oid)))
    if order_oid and "order_oid" in tbl.c:
        stmt = stmt.where(tbl.c.order_oid.in_(_csv_ids(order_oid)))
    date_col = (
        tbl.c["lst_dt_go"]
        if (date_field == "go_date" and "lst_dt_go" in tbl.c)
        else tbl.c[spec.date_col]
    )
    if date_from:
        stmt = stmt.where(date_col >= date_from)
    if date_to:
        stmt = stmt.where(date_col < date_to + "~")
    # 有無外部評論：有 review_external_lst_oid 且有實際內容（sentiment 或 free_tag 非空）。與前端顯示一致
    # （v-if ext_sentiment || ext_free_tag.length）。未匹配列 upsert 後三欄皆空字串 ''（非 NULL），故
    # isnot(None) 不足——須同時排除 ''（free_tag 另排空陣列 '[]'/'null'），否則空字串列誤判為「有」。
    # lst_oid 條件為語義防護（內容恆隨 lst_oid 而來，無孤兒內容列）。僅 product_reviews 有融合欄，餘忽略。
    if has_external is not None and "review_external_lst_oid" in tbl.c:
        has_content = or_(
            and_(tbl.c["sentiment"].isnot(None), tbl.c["sentiment"] != ""),
            and_(tbl.c["free_tag"].isnot(None), tbl.c["free_tag"].notin_(["", "[]", "null"])),
        )
        ext_cond = and_(
            tbl.c["review_external_lst_oid"].isnot(None),
            tbl.c["review_external_lst_oid"] != "",
            has_content,
        )
        stmt = stmt.where(ext_cond if has_external else ~ext_cond)
    return stmt


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

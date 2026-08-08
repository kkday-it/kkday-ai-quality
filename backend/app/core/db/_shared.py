"""db 子模組共用：prejudge.json/verdict.json 顯示標籤 / 信心閾值 + 複合鍵 join + 商品垂直分類 + 時間格式化。

problems / prejudge_targets / attribution / export 多處共用，抽出為單一真相（Rule of Three）。
初判顯示 label + 信心閾值 SSOT＝config/ai_judge/prejudge.json（+verdict.json）（前後端同讀）；db 不能 import settings
（settings 已 import db → 循環），故以 paths.AI_JUDGE_DIR 自讀該檔。
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping

from sqlalchemy import Table, and_, exists, false, or_, select
from sqlalchemy.sql import Select

from app.core.db import source_registry
from app.core.db import tables as T
from app.core.judge_config.ai_judge import domain_owner as _domain_owner
from app.core.paths import AI_JUDGE_DIR as _AI_JUDGE_DIR

# ── 初判顯示標籤 + 信心閾值（prejudge.json/verdict.json）───────────────
# 皆為 module 級 dict，熱重載時就地 clear+update（不重綁），使既有 import 引用（attribution/export）
# 同步反映新值、無需改呼叫端。SSOT＝config/ai_judge/prejudge.json（+verdict.json）（專案靜態設定檔），改值＝改檔 + 重啟
# （或呼叫 reload_pipeline_cfg 熱重載）。
_DEFAULT_TIERS: dict = {"auto_accept": 0.8, "jury_low": 0.5, "jury_high": 0.7}
_POLARITY_LABEL_ZH: dict[str, str] = {}
_TIER_LABEL_ZH: dict[str, str] = {}
_STAGE_LABEL_ZH: dict[str, str] = {}
_CONFIDENCE_TIERS: dict = {}


def _apply_pipeline_cfg(cfg: dict) -> None:
    """將初判/判決合併配置就地灌入 module 級 label / 閾值 dict（clear+update 保持同一物件引用）。"""
    _POLARITY_LABEL_ZH.clear()
    _POLARITY_LABEL_ZH.update(cfg.get("polarity_labels", {}))
    _TIER_LABEL_ZH.clear()
    _TIER_LABEL_ZH.update(cfg.get("tier_labels", {}))
    _STAGE_LABEL_ZH.clear()
    _STAGE_LABEL_ZH.update(cfg.get("stage_labels", {}))
    _CONFIDENCE_TIERS.clear()
    _CONFIDENCE_TIERS.update(cfg.get("confidence_tiers", _DEFAULT_TIERS))


def _read_stage_files() -> dict:
    """讀兩階段設定檔並合併為單一 dict（import 期安全來源；DB 引擎未必就緒時用）。

    config/ai_judge/prejudge.json（初判層：極性閘門/證據政策/信心閾值/初判旋鈕/stage·tier·
    polarity labels）＋ verdict.json（G1 自動採納路由旋鈕）——兩檔鍵不重疊，
    合併後形狀與消費端既有預期一致（單一 dict）。
    """
    merged: dict = {}
    for name in ("prejudge.json", "verdict.json"):
        try:
            merged.update(json.loads((_AI_JUDGE_DIR / name).read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    merged.pop("_comment", None)  # 各檔說明註解不進 runtime dict
    return merged


def read_pipeline_config() -> dict:
    """讀初判＋判決兩階段配置（prejudge.json + verdict.json 合併）。

    兩檔皆為**專案靜態設定檔**（不進 RULE_CODES、不 DB 版本化 / 不列規則頁）——直讀檔案即單一
    真相源。改值＝改檔 + 重啟（或 reload_pipeline_cfg 熱重載）。保留此函式為兩階段配置讀取的
    **單一入口**（_shared 熱重載、初判旋鈕快取共用；Rule of Three）。
    """
    return _read_stage_files()


def reload_pipeline_cfg() -> None:
    """熱重載 judgment 配置（規則管理存檔後由 rules._reload_judge_cache 呼叫，對齊 ai_judge）。

    就地更新 label / 閾值 dict（讀 config/ai_judge/prejudge.json（+verdict.json），見 read_pipeline_config），使 import 引用免改碼即反映新值。
    """
    _apply_pipeline_cfg(read_pipeline_config())


# import 期以 seed 檔初始化（DB 引擎未必就緒；DB active 熱更新由 reload_pipeline_cfg 於 runtime 觸發）。
_apply_pipeline_cfg(_read_stage_files())


# ── 初判 API DTO：attributions typed 欄 → 乾淨巢狀物件（storage=typed 欄；呈現=巢狀 DTO 的 SSOT）──
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
    """attributions 列（typed 欄 mapping）→ 一條歸因的乾淨巢狀 DTO（API/前端 SSOT）。

    r 為含初判欄的 mapping（fan-out 走 jg_ 前綴 → 呼叫端先 unwrap 成無前綴 dict 再傳入，
    或直接傳 attributions 列 mapping）。

    Args:
        r: 初判欄 mapping（attribution_oid/polarity/prejudge_stage/l1_code…/conf_value/summary…）。

    Returns:
        巢狀 DTO：{attribution_oid, polarity, stage, l1/l2:{code,label},
        confidence:{value,raw,tier}, content:{summary,evidence,action},
        owner, model, is_primary, is_auto_accepted}。
    """
    l1_code = r.get("l1_code")
    summary_langs = _summary_langs(r.get("summary"))
    return {
        "attribution_oid": r.get("attribution_oid"),
        "polarity": r.get("polarity"),
        "sentiment_score": r.get(
            "sentiment_score"
        ),  # 我方情緒分 1-5（與外部評論 sentiment 同尺度）
        "stage": r.get("prejudge_stage"),
        "l1": {"code": l1_code, "label": r.get("l1_label")},
        "l2": {"code": r.get("l2_code"), "label": r.get("l2_label")},
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
        # ⚠️ 與判決歸因的 responsible_party 語義重疊、來源不同——補判決功能時必須收斂成一個。
        "owner": _domain_owner(l1_code or ""),
        # ── 人工介入（現值來源／複審狀態；is_deleted 刻意不出 wire）──
        # origin＝顯示來源的 SSOT：前端據此決定顯示「人工 · 修改者」還是初判 model，
        # 判斷收在服務端不散在 template（需求：糾正後顯示來源不再是 model 而是人工修改者）。
        "origin": "human" if (r.get("is_manual_created") or r.get("is_human_corrected")) else "ai",
        "is_manual_created": bool(r.get("is_manual_created")),
        "is_human_corrected": bool(r.get("is_human_corrected")),
        # 人工新增列從未 modify 過，故 modify_user 回退 create_user
        "corrected_by": r.get("modify_user") or r.get("create_user"),
        "corrected_at": _iso_if_dt(r.get("modify_date")),
        "correction_reason": r.get("correction_reason"),
        "review_status": r.get("review_status"),
        "model": r.get("model"),  # 初判模型（stub / ensemble 同 attributions.model 語意）
        "is_primary": r.get("is_primary"),
        "is_auto_accepted": r.get("is_auto_accepted"),  # G1 系統自動採納旗標
    }


def live_attr_cond():
    """「這條歸因仍是現值」的述詞：排除人工標記為 AI 誤判的 tombstone 列。

    ⚠️ 刻意寫成 `== false` 而非 `.is_(False)`：`attribution_tbl` 的 6 條篩選索引是
    `WHERE is_deleted = false` 的 partial index，PG 的 predicate implication 對 `IS false`
    不保證能推導出等價——寫錯的下場是索引靜默失效（不報錯，只是慢），見 tables.py 該段註解。
    """
    return T.attributions.c.is_deleted == false()


def human_touched_cond():
    """「這則反饋已被人工介入」的述詞（人工託管判定）。

    任一列被人工新增／改值／標記誤判／**複審確認**，整則反饋即進入人工託管——重新初判不再覆蓋
    `attribution_tbl`，LLM 結果轉入 `attribution_suggestion_lst` 待審（見 findings.replace_source_findings）。
    補判決功能時只要在此 `or_()` 加 `is_verdicted == True`，其餘機制全部沿用。

    **`review_status == 'confirmed'` 也算人工介入**（2026-08-07 拍板）：少了這條，「人說過這條 AI
    判對了」不會鎖住該反饋，下次重新初判走 AI 託管分支整組 DELETE，複審記錄隨列消失——複審做完
    等於白做。用 `== 'confirmed'` 而非 `!= 'unreviewed'`：`'corrected'` 的列已被 `is_human_corrected`
    涵蓋，精確表達比寬鬆表達好讀。
    """
    jg = T.attributions
    return or_(
        jg.c.is_manual_created,
        jg.c.is_human_corrected,
        jg.c.is_deleted,
        jg.c.review_status == "confirmed",
    )


def _jg_join_cond(spec, *, include_deleted: bool = False):
    """attributions 與來源表的複合鍵 join 條件：source + source_id == 該表特徵 id 欄。

    **預設排除 tombstone**（`include_deleted=False`）——本函式與 `_jg_exists` 是 12 個歸因查詢點
    中 10 個的必經 chokepoint，把過濾放在這裡即一次到位，不必在每個呼叫端散改（散改漏一處的
    後果是靜默的數字變大，不會報錯）。

    Args:
        spec: 來源表 spec（table + natural_key + source）。
        include_deleted: True＝連人工標記誤判的列也算。**目前唯一合法用途是
            `prejudge_targets`**：那裡問的是「這則反饋判過沒有」，tombstone 算判過；
            若排除它，所有歸因都被標記誤判的反饋會被 scope=unjudged 永遠重複撈取。
    """
    jg = T.attributions
    cond = and_(jg.c.source == spec.source, jg.c.source_id == spec.table.c[spec.natural_key])
    return cond if include_deleted else and_(cond, live_attr_cond())


def _jg_exists(spec, *extra, include_deleted: bool = False):
    """`EXISTS (SELECT 1 FROM attributions WHERE source=X AND source_id=特徵id [AND ...])`。

    預設排除 tombstone，語義與參數見 `_jg_join_cond`。
    """
    return exists().where(and_(_jg_join_cond(spec, include_deleted=include_deleted), *extra))


def _csv_ids(value: str) -> list[str]:
    """逗號分隔 id 字串 → 去空白去空的清單（「1, 2 ,3」→ ['1','2','3']）；單值回單元素清單。"""
    return [p.strip() for p in str(value).split(",") if p.strip()]


def _vertical_codes(vertical: str | list[str] | None) -> list[str]:
    """商品垂直分類（BD Vertical 名稱，如 Tour/Trans）→ bd_tag 代碼清單（多值 extend 合併；空/None 回空清單）。

    局部 import：bd_tag_vertical loader 讀 db.get_rule_active → 頂層 import 會造成循環依賴。
    供 list_problems / overview / breakdown / prejudge_targets 共用（比對 spec.bd_tag_col 的 IN 篩選）。
    """
    if not vertical:
        return []
    from app.core import bd_tag_vertical as _bd_tag_vertical

    verticals = [vertical] if isinstance(vertical, str) else list(vertical)
    codes: list[str] = []
    for v in verticals:
        codes.extend(_bd_tag_vertical.codes_for_vertical(v))
    return codes


def _vertical_scoped_spec(
    source: str | None, vertical: str | list[str] | None
) -> source_registry.SourceSpec | None:
    """歸因聚合（overview/breakdown）選表：source 命中拆表來源用其 spec；否則 source=None（縱覽全部）
    但帶商品垂直分類篩選時，改走 conversations（reviews 與 conversations 現在都具 bd_tag_col，
    conversations 資料量較大且欄位齊全，維持原有單一來源 fallback 慣例）。

    有篩選時只統計「有 bd_tag 且落在所選 Vertical」的資料，無 bd_tag 來源（工單等）在有篩選時排除。
    無篩選則回 None，呼叫端走 attributions 直接聚合維持「全部來源」語義。
    """
    spec = source_registry.spec_for(source)
    if spec is None and _vertical_codes(vertical):
        spec = source_registry.spec_for("conversations")
    return spec


def apply_table_filters(
    spec,
    stmt,
    *,
    vertical: str | list[str] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    date_field: str = "occurred_at",
    rec_oid: str | None = None,
    prod_oid: str | None = None,
    order_oid: str | None = None,
    has_external: bool | None = None,
):
    """來源表級篩選 SSOT（商品垂直分類/日期區間/關聯 oid/有無外部評論）——統一問題列表與初判目標選取共用。

    僅含「來源表自身欄位」的條件；初判級條件（polarity/stage/tier/歸因分類）因兩端結構不同
    （列表用 EXISTS、目標選取用 join 分支）由各呼叫端自行套。語義逐條對齊 list_problems：
    - vertical：Vertical 名稱經 bd_tag_vertical.codes_for_vertical 展開為 BD 分工代碼，
      直接對 spec.bd_tag_col 做 IN 比對（bd_tag_cd 為扁平代碼欄，非 JSON，不需再 cast 抽 key）。
    - 日期：sargable 比較走 btree 索引；上界半開 `< date_to||'~'` 含當日整天。
      date_field='go_date' 且表有 go_date 用之，否則 spec.date_col。
    - rec_oid/prod_oid/order_oid：表有對應欄才生效。
    - has_external：有無外部評論融合資料（僅有 review_external_lst_oid 欄的來源生效，如 reviews）。
    """
    from sqlalchemy import and_, or_

    tbl = spec.table
    if spec.bd_tag_col:
        codes = _vertical_codes(vertical)
        if codes:
            stmt = stmt.where(tbl.c[spec.bd_tag_col].in_(codes))
    # rec_oid/prod_oid/order_oid：支援逗號分隔多值（「1,2,3」→ IN 一起查）；單值＝IN 單元素。
    if rec_oid and spec.natural_key in tbl.c:
        stmt = stmt.where(tbl.c[spec.natural_key].in_(_csv_ids(rec_oid)))
    if prod_oid and "prod_oid" in tbl.c:
        stmt = stmt.where(tbl.c.prod_oid.in_(_csv_ids(prod_oid)))
    if order_oid and "order_oid" in tbl.c:
        stmt = stmt.where(tbl.c.order_oid.in_(_csv_ids(order_oid)))
    date_col = (
        tbl.c["go_date"]
        if (date_field == "go_date" and "go_date" in tbl.c)
        else tbl.c[spec.date_col]
    )
    if date_from:
        stmt = stmt.where(date_col >= date_from)
    if date_to:
        stmt = stmt.where(date_col < date_to + "~")
    # 有無外部評論：有 review_external_lst_oid 且有實際內容（sentiment 或 free_tag 非空）。與前端顯示一致
    # （v-if ext_sentiment || ext_free_tag.length）。未匹配列 upsert 後三欄皆空字串 ''（非 NULL），故
    # isnot(None) 不足——須同時排除 ''（free_tag 另排空陣列 '[]'/'null'），否則空字串列誤判為「有」。
    # lst_oid 條件為語義防護（內容恆隨 lst_oid 而來，無孤兒內容列）。僅 reviews 有融合欄，餘忽略。
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


def _iso_if_dt(value):
    """datetime/date → ISO 字串；其餘原樣返回（對齊專案時間欄以字串出 API 的慣例）。"""
    return value.isoformat() if value is not None and hasattr(value, "isoformat") else value


def select_wire(table: Table) -> Select:
    """`select(表)` 的 wire 安全版：每欄 label 成 **Python key**，而非 DB 欄名。

    為什麼需要：DDL 規範對齊後 34 個欄的 DB 名與 Python key 刻意不同
    （`Column("feedback_source_code", key="source")`）。`key=` 只在**組查詢**時生效——
    讀結果時 SQLAlchemy 的 result mapping 一律用 **DB 欄名**，於是 `select(表)` 全欄直出的
    端點會把規範名直接洩到 wire 上（`source` 變成 `feedback_source_code`），前端當場斷。

    本函式把「DB 用規範名、wire 用原名」這件事收斂成單一出口：全欄直出的讀取函式一律改用它，
    契約由 `tests/test_wire_contract.py` 的凍結快照守住。
    """
    return select(*[c.label(c.key) for c in table.columns])


def wire_row(row: Mapping, spec: Mapping[str, str]) -> dict:
    """DB 列 mapping → wire dict（顯式白名單）。`spec` 為 {wire 鍵: DB 欄名}。

    **存在的理由**：多處讀取函式以 `select(表)` 全欄直出，等於「DB 加一個欄」自動變成
    「API 契約變更」——內部狀態（稽核欄、內部旗標）會無聲流到前端，且型別檢查與測試都攔不住。
    改走白名單後，DB schema 演進與 wire 契約變更成為兩個各自顯式的動作；`spec` 本身即可被
    快照測試凍結的契約宣告（見 `tests/test_wire_contract.py`）。

    DB 欄名寫錯時**直接拋錯**而非靜默給 None——後者會讓該欄在前端變空白且無跡可循。

    Args:
        row: DB 列 mapping（須含 spec 右側的全部欄）。
        spec: {wire 鍵: DB 欄名} 映射；wire 鍵即 API 回傳的 key。

    Returns:
        僅含 spec 所列鍵的 dict；時間欄已轉 ISO 字串。

    Raises:
        KeyError: spec 引用了 row 沒有的欄名。
    """
    missing = [col for col in spec.values() if col not in row]
    if missing:
        raise KeyError(f"wire_row spec 引用了列中不存在的欄：{missing}")
    return {wire: _iso_if_dt(row[col]) for wire, col in spec.items()}


def fmt_datetime(value, *, date_only: bool = False) -> str:
    """正規化時間字串：去毫秒/去 T·Z；date_only 或時間為 00:00:00 時只留日期。

    來源 raw 時間格式不一（'2026-06-25 07:46:19.810' / ISO 'T...Z'）→ 統一可讀格式，
    導出與前端共用此語義（前端另有同名 JS helper）。非時間字串原樣返回（不誤傷）。
    """
    s = str(value).strip().replace("T", " ")
    if s.endswith("Z"):
        s = s[:-1].strip()
    s = re.sub(r"\.\d+", "", s)  # 去小數秒（.810）
    s = re.sub(r"[+-]\d{2}:\d{2}$", "", s).strip()  # 去尾綴時區（isoformat +00:00）
    if date_only or s.endswith(" 00:00:00"):
        return s.split(" ")[0]
    return s

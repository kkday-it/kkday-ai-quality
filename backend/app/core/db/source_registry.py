"""反饋來源 → 實體表 registry（來源選表 SSOT）。

5 反饋來源皆已拆為獨立實體表（見 tables.py）：reviews / conversations /
freshdesk_tickets / app_feedback / mixpanel_tracker，各以特徵 id 為 natural_key。
本模組登記每個來源的 table + natural_key + score_col/bd_tag_col/date_col，供 db 子模組
統一 spec 驅動查詢（source=None＝縱覽全部，走 attributions 直接聚合，非單表）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import Table

from app.core.db import tables as T


@dataclass(frozen=True)
class SourceSpec:
    """已拆表來源的查詢規格：選表 + 各語意欄位在該表的實際欄名。"""

    source: str
    table: Table
    natural_key: str  # 自然鍵欄名（upsert 衝突目標）
    score_col: str | None = None  # 星等/評分欄名（list_problems score 篩選用）
    bd_tag_col: str | None = None  # BD 分工代碼欄名（商品垂直分類篩選用，見 bd_tag_vertical）
    date_col: str = "occurred_at"  # 預設日期篩選欄（date_field='occurred_at' 對應）
    # 上傳表頭 → DB 欄名的**例外**映射；未列者一律恆等（表頭即欄名）。
    # 只放「表頭不可能等於欄名」的結構性別名（如 $ 開頭、大寫），業務可調的映射不放這裡。
    header_aliases: dict[str, str] = field(default_factory=dict)


# 5 反饋來源登記（value=source code → SourceSpec）。各表對齊源 schema、PK=特徵 id；
# canonical 顯示欄映射走 config/ai_judge/source_mapping.json 的 field_map（源欄→canonical）。
# score_col/bd_tag_col/date_col 為「該來源實際源欄名」（供 list score 篩選 / vertical 篩選 / 日期排序）。
# bd_tag_col 一律指向「代碼欄」bd_tag_cd（bd_tag 為中文文字，不作篩選鍵）——
# reviews 與 conversations 兩份 BQ 取數 SQL 已統一此命名。
_REGISTRY: dict[str, SourceSpec] = {
    "reviews": SourceSpec(
        source="reviews",
        table=T.reviews,
        natural_key="rec_oid",
        score_col="rec_scores",
        bd_tag_col="bd_tag_cd",
        date_col="create_date",
    ),
    "conversations": SourceSpec(
        source="conversations",
        table=T.conversations,
        natural_key="session_oid",
        bd_tag_col="bd_tag_cd",
        date_col="inbound_time",
    ),
    "freshdesk_tickets": SourceSpec(
        source="freshdesk_tickets",
        table=T.freshdesk_tickets,
        natural_key="id",
        score_col="st_survey_rating",
        date_col="created_at",
    ),
    "app_feedback": SourceSpec(
        source="app_feedback",
        table=T.app_feedback,
        natural_key="oid",
        score_col="score",
        date_col="created_datetime",
    ),
    "mixpanel_tracker": SourceSpec(
        source="mixpanel_tracker",
        table=T.mixpanel_tracker,
        natural_key="insert_id",
        date_col="time",
        # Mixpanel 匯出的表頭帶 $ 前綴 / 大寫，不是合法 SQL 識別字，落庫前必須改名。
        # 原本寫死在 `judge/ingest/upload_batch.py`，只有上傳路徑看得到；移來此處後
        # 校驗與寫入共用同一份宣告，不會再出現「校驗說可以、寫入卻對不上」。
        header_aliases={
            "$insert_id": "insert_id",
            "$distinct_id": "distinct_id",
            "$current_url": "current_url",
            "$os": "os",
            "Platform": "platform",
        },
    ),
}


def header_column_map(source: str | None) -> dict[str, str]:
    """該來源「上傳表頭 → DB 欄名」的完整映射；未知來源回空 dict。

    ⚠️ **為什麼要有這個函式**：上傳寫入路徑原本以 `[c.name for c in tbl.columns]` 直接拿 DB 欄名
    去 `row.get(欄名)`，等於默認「CSV 表頭逐字等於 DB 欄名」。一旦上游改表頭或我方改欄名，
    對不上的欄會**靜默落 NULL**——不報錯、`inserted` 照樣計數，資料看起來匯進去了其實是空的。
    把映射變成一份可被查詢的宣告後，校驗端才能在上傳前把「對不上的表頭」指出來。

    當前為恆等映射 + `header_aliases` 例外；日後 DB 欄改名時，改的是這裡而非上游檔案格式。

    Returns:
        {表頭: DB 欄名}；含該表全部欄位的恆等項與宣告的別名項。
    """
    spec = spec_for(source)
    if spec is None:
        return {}
    return {c.name: c.name for c in spec.table.columns} | dict(spec.header_aliases)


def spec_for(source: str | None) -> SourceSpec | None:
    """依來源 code 取其拆表規格；未拆表 / None / 未知來源一律回 None（呼叫端 fallback 舊邏輯）。

    Args:
        source: 來源 code（如 'reviews'）；None 表示不限定來源。

    Returns:
        該來源的 SourceSpec；未命中回 None。
    """
    if not source:
        return None
    return _REGISTRY.get(source)

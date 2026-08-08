"""人工介入地基：attribution_tbl 加人工欄 + 待審建議表 + 判決值域主檔

AI 判錯時質檢人員完全無法修正——`attribution_tbl` 的表註解直接寫著「無任何人工可改欄位，
重新初判即整組替換」，全後端零個 PUT/PATCH/DELETE。人只能重跑 AI，不能告訴系統「你錯了」。
本 migration 是修這件事的地基。

**核心不變式（一則反饋只有兩種託管狀態）**：

- **AI 託管**（無任何人工痕跡）→ 重新初判＝今天的行為，一字不改
- **人工託管**（任一列 is_manual_created / is_human_corrected / is_deleted）→ 重新初判完全不碰
  `attribution_tbl`，LLM 結果轉入 `attribution_suggestion_lst` 待人工採納

這條不變式讓「人工改分類後 AI 新結果撞自然鍵」在物理上不可能發生（人工託管下 AI 一列都不寫），
故 `idx_attribution_tbl_unique01` 原封不動。既有 6,321 列本次全部落成 AI 託管 → **全庫重新初判
行為零變化**，這是本次改動最大的安全邊際。

⚠️ **6 條篩選索引改 partial（`WHERE is_deleted = false`）**：tombstone 上線後每條讀取路徑都多帶
`is_deleted = false`，該欄不在索引裡的話 planner 必須回堆表驗證，`tables.py` 那段實測到的
Index Only Scan（Heap Fetches 0）會退化。partial 化讓述詞恆真於索引內容，index-only 得以保住。
查詢端述詞必須渲染成 `= false`（`sa.false()`）而非 `IS false`，否則 PG 的 predicate implication
不保證推導出等價，索引會**靜默失效不報錯**。

⚠️ 加欄用 `ADD COLUMN ... NOT NULL DEFAULT`：PG 11+ 不重寫表、既有列即時填值，不需要
「先 nullable → UPDATE → SET NOT NULL」三步。若倒過來先 nullable，既有 6,321 列會短暫為 NULL，
而讀取層的 `is_deleted = false` 會把它們全部排除——列表當場空白。

⚠️ 判決值域種子為**凍結快照**（自 `config/ai_judge/attribution_dimension.json` 導出後寫死），
不在執行期讀該檔：migration 一旦讀當下的 config，「這支 revision 做了什麼」就不再可重現。

Revision ID: f4c62a9b17e0
Revises: e8c2b5f14a09
Create Date: 2026-08-06

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "f4c62a9b17e0"
down_revision: str | Sequence[str] | None = "e8c2b5f14a09"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PARTIAL_INDEXED_COLS: tuple[str, ...] = (
    "polarity",
    "prejudge_stage",
    "l1_code",
    "l2_code",
    "sentiment_score",
    "conf_tier",
)

_ATTR_COLUMN_COMMENTS: dict[str, str] = {
    "is_manual_created": "人工手動新增的歸因（AI 未產出、由人補上）",
    "is_human_corrected": "AI 產出後由人工改過值（分類／傾向／情緒分）",
    "is_deleted": "人工標記為 AI 誤判（tombstone）：所有讀取路徑排除，但**保留列以佔住自然鍵**——"
    "使「重新初判把人工刪掉的歸因悄悄復活」在物理上不可能。還原走 restore",
    "correction_reason": "最近一次人工糾正／刪除的理由（提交時強制必填）；歷次理由完整保存於 "
    "attribution_event_lst 的 kind='correction' 事件",
    "review_status": "人工複審狀態：unreviewed（未複審）/ confirmed（人工確認 AI 判對）/ "
    "corrected（人工已糾正）——補上 pending_review 進來後沒有出口的缺口",
}

_ATTR_TABLE_COMMENT = (
    "初判歸因結果（一列＝一條歸因，同一則反饋可有多列）。全 typed scalar 欄無 JSONB blob——"
    "本表是查詢／聚合／篩選密集的分析核心，typed 欄可直接 btree 索引且 SQL 乾淨；巢狀物件屬呈現層，"
    "在 API DTO（_shared.attribution_dto）才組。"
    "一則反饋有兩種託管狀態：**AI 託管**（無任何人工痕跡）重新初判即整組替換；**人工託管**"
    "（任一列 is_manual_created / is_human_corrected / is_deleted）重新初判完全不碰本表，"
    "LLM 結果轉入 attribution_suggestion_lst 待人工採納"
)

# 判決值域種子（凍結快照，見檔頭 ⚠️）：(dimension_code, item_code, item_label, item_desc, sort_order)
_DIMENSION_SEED: tuple[tuple[str, str, str, str, int], ...] = (
    (
        "responsible_party",
        "supplier",
        "供應商",
        "供應商未依約履行：現場服務、車輛、導遊、餐食等實際交付端的問題",
        0,
    ),
    (
        "responsible_party",
        "product",
        "商品內容",
        "商品頁描述與實際不符、規格漏寫或誤導——問題在上架內容而非現場執行",
        1,
    ),
    ("responsible_party", "service", "客服", "客服回應速度、正確性、態度；含未處理與處理錯誤", 2),
    ("responsible_party", "platform", "平台系統", "訂購流程、付款、通知、App/網站功能異常", 3),
    (
        "responsible_party",
        "customer",
        "旅客自身",
        "旅客誤解、遲到、未依規定準備文件等——非我方可控",
        4,
    ),
    ("responsible_party", "none", "無責任方", "正向回饋或中立陳述，不涉及究責", 5),
    ("severity", "P0", "P0 緊急", "已造成金錢損失、人身安全疑慮或大量客訴，需當日處理", 0),
    ("severity", "P1", "P1 高", "明確影響旅客體驗且會持續發生，需本週處理", 1),
    ("severity", "P2", "P2 中", "體驗瑕疵，可排入常規改善", 2),
    ("severity", "P3", "P3 低", "輕微或個案，觀察即可", 3),
    ("verdict_action", "rewrite_field", "改寫商品欄位", "商品頁某欄描述有誤或不清，需重寫", 0),
    (
        "verdict_action",
        "fix_contradiction",
        "修正前後矛盾",
        "商品頁不同區塊互相衝突，需統一口徑",
        1,
    ),
    ("verdict_action", "add_missing_info", "補充缺漏資訊", "旅客需要但頁面沒寫的資訊", 2),
    ("verdict_action", "clarify_wording", "釐清用語", "措辭易生誤解，需改得更明確", 3),
    (
        "verdict_action",
        "penalize_breach",
        "計點違規並要求改善",
        "供應商履約不符，走違規計點流程",
        4,
    ),
    ("verdict_action", "escalate_ops", "轉營運處理", "需營運端介入的個案", 5),
    ("verdict_action", "escalate_ux", "轉產品體驗處理", "系統或流程層面的體驗問題", 6),
    ("verdict_action", "no_action", "無需行動", "正向回饋或無可改善之處", 7),
)


def _sql_str(value: str) -> str:
    """字串常值跳脫（單引號 → 雙寫）：註解文字含 `kind='correction'` 這種內嵌引號。"""
    return value.replace("'", "''")


def upgrade() -> None:
    """加人工欄 → 索引 partial 化 → 建兩張新表 → 灌判決值域種子。"""
    # ── ① attribution_tbl 加 5 欄（IF NOT EXISTS 為 adopt 路徑冪等，比照 b2f47c9e15a3）──
    for name, ddl_type in (
        ("is_manual_created", "BOOLEAN NOT NULL DEFAULT false"),
        ("is_human_corrected", "BOOLEAN NOT NULL DEFAULT false"),
        ("is_deleted", "BOOLEAN NOT NULL DEFAULT false"),
        ("correction_reason", "TEXT"),
        ("review_status", "TEXT NOT NULL DEFAULT 'unreviewed'"),
    ):
        op.execute(f"ALTER TABLE attribution_tbl ADD COLUMN IF NOT EXISTS {name} {ddl_type}")

    # ── ② 6 條篩選索引 partial 化 + 人工託管判定索引（見檔頭 ⚠️）──
    for col in _PARTIAL_INDEXED_COLS:
        op.drop_index(f"idx_attribution_tbl_{col}", table_name="attribution_tbl")
        op.create_index(
            f"idx_attribution_tbl_{col}",
            "attribution_tbl",
            [col],
            postgresql_where=sa.text("is_deleted = false"),
        )
    op.create_index(
        "idx_attribution_tbl_mix02",
        "attribution_tbl",
        ["feedback_source_code", "source_id"],
        postgresql_where=sa.text("is_manual_created OR is_human_corrected OR is_deleted"),
    )

    # ── ③ 註解（test_schema_parity 逐字比對，必須與 tables.py 完全一致）──
    # ⚠️ 註解本身含單引號（如 `kind='correction'`），必須跳脫成 '' 否則 SQL 字串當場斷掉
    for col, note in _ATTR_COLUMN_COMMENTS.items():
        op.execute(f"COMMENT ON COLUMN attribution_tbl.{col} IS '{_sql_str(note)}'")
    op.execute(f"COMMENT ON TABLE attribution_tbl IS '{_sql_str(_ATTR_TABLE_COMMENT)}'")

    # ── ④ 待審建議表 ──
    op.create_table(
        "attribution_suggestion_lst",
        sa.Column(
            "attribution_suggestion_oid",
            sa.Integer(),
            sa.Identity(),
            nullable=False,
            comment="流水號主鍵（serial）",
        ),
        sa.Column(
            "feedback_source_code", sa.Text(), nullable=False, comment="反饋來源 code（reviews…）"
        ),
        sa.Column("source_id", sa.Text(), nullable=False, comment="該來源的特徵 id（評論級鍵）"),
        sa.Column(
            "suggestion_batch_id",
            sa.Text(),
            nullable=False,
            comment="一次重新初判對一則反饋產生的一組建議（整組採納／事件關聯用）",
        ),
        sa.Column(
            "change_type",
            sa.Text(),
            nullable=False,
            comment="變更型別：replace（同 L1/L2 面向但值有異）/ add（AI 新發現的面向）/ "
            "remove（AI 認為現值面向不再成立）",
        ),
        sa.Column(
            "attribution_oid",
            sa.Integer(),
            comment="對應的現值列（replace / remove 有值；add 為空）。軟關聯無 FK",
        ),
        sa.Column("polarity", sa.Text(), comment="建議的情緒傾向"),
        sa.Column("sentiment_score", sa.Integer(), comment="建議的情緒分 1-5"),
        sa.Column("l1_code", sa.Text(), comment="建議的 L1 域代碼"),
        sa.Column("l1_label", sa.Text(), comment="建議的 L1 域中文名（產生當下的快照）"),
        sa.Column("l2_code", sa.Text(), comment="建議的 L2 面向代碼"),
        sa.Column("l2_label", sa.Text(), comment="建議的 L2 面向中文名（產生當下的快照）"),
        sa.Column("conf_value", sa.Float(), comment="建議的信心值（校準後）"),
        sa.Column("conf_raw", sa.Float(), comment="建議的原始信心值"),
        sa.Column("conf_tier", sa.Text(), comment="建議的信心分層"),
        sa.Column(
            "summary",
            postgresql.JSONB(astext_type=sa.Text()),
            comment="建議的反饋摘要（語系→摘要 map）",
        ),
        sa.Column("evidence", sa.Text(), comment="建議的佐證原文"),
        sa.Column("recommended_action", sa.Text(), comment="建議的建議行動"),
        sa.Column("model", sa.Text(), comment="產生本建議的初判模型"),
        sa.Column(
            "job_id", sa.Text(), comment="產生本建議的初判任務 id（與 prejudge_runs.job_id 對齊）"
        ),
        sa.Column("create_user", sa.String(255), comment="觸發本次重新初判的人"),
        sa.Column(
            "create_date",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            comment="建議產生時間",
        ),
        sa.PrimaryKeyConstraint("attribution_suggestion_oid"),
        comment="待審 LLM 建議（人工託管的反饋重新初判時，AI 結果不寫 attribution_tbl 而轉入本表）。"
        "本表語義是「**當前尚未處理**的建議」：採納／駁回即刪除該列（決策本身記在 attribution_event_lst "
        "的 kind='suggestion_resolved' 事件），再次重新初判即先清光舊 pending 再插新的——故刻意"
        "沒有 review_status 狀態機",
    )
    op.create_index(
        "idx_attribution_suggestion_lst_mix01",
        "attribution_suggestion_lst",
        ["feedback_source_code", "source_id"],
    )
    op.create_index(
        "idx_attribution_suggestion_lst_unique01",
        "attribution_suggestion_lst",
        ["suggestion_batch_id", "l1_code", "l2_code"],
        unique=True,
    )
    op.create_index(
        "idx_attribution_suggestion_lst_create_date",
        "attribution_suggestion_lst",
        ["create_date"],
    )

    # ── ⑤ 判決值域主檔 ──
    op.create_table(
        "attribution_dimension_master",
        sa.Column(
            "attribution_dimension_oid",
            sa.Integer(),
            sa.Identity(),
            nullable=False,
            comment="流水號主鍵（serial）",
        ),
        sa.Column(
            "dimension_code",
            sa.Text(),
            nullable=False,
            comment="值域維度：responsible_party（責任方）/ severity（嚴重度）/ "
            "verdict_action（建議行動）",
        ),
        sa.Column(
            "item_code",
            sa.Text(),
            nullable=False,
            comment="項目機器碼（落入判決欄的值；改碼＝改歷史語義，禁改）",
        ),
        sa.Column(
            "item_label",
            sa.Text(),
            nullable=False,
            comment="項目中文名（顯示用可改；判決落庫時同存快照，改名不回溯污染歷史判決）",
        ),
        sa.Column("item_desc", sa.Text(), comment="判準說明（給定責的人看的口徑描述）"),
        sa.Column(
            "sort_order",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="同維度內顯示排序（小在前）",
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default="true",
            comment="是否可選（停用不刪——硬刪已被歷史判決引用的 code 會讓那些列顯示空白）",
        ),
        sa.Column("create_user", sa.String(255), comment="建立者"),
        sa.Column(
            "create_date",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            comment="建立時間",
        ),
        sa.Column("modify_user", sa.String(255), comment="最後修改者"),
        sa.Column("modify_date", sa.DateTime(timezone=True), comment="最後修改時間"),
        sa.PrimaryKeyConstraint("attribution_dimension_oid"),
        comment="判決歸因值域主檔（責任方／嚴重度／建議行動三軸共用一表，以 dimension_code 判別）。"
        "三者欄形完全相同，拆三張表＝三套 migration／API／畫面；判別式單表是既有慣例"
        "（judge_rule_version_lst 用 rule_code 判別）。檔案 config/ai_judge/attribution_dimension.json "
        "為默認 seed，本表存 live",
    )
    op.create_index(
        "idx_attribution_dimension_master_unique01",
        "attribution_dimension_master",
        ["dimension_code", "item_code"],
        unique=True,
    )
    op.create_index(
        "idx_attribution_dimension_master_mix01",
        "attribution_dimension_master",
        ["dimension_code", "sort_order"],
    )

    # ── ⑥ 值域種子（ON CONFLICT 冪等：adopt 路徑或重跑皆安全）──
    dim = sa.table(
        "attribution_dimension_master",
        sa.column("dimension_code", sa.Text),
        sa.column("item_code", sa.Text),
        sa.column("item_label", sa.Text),
        sa.column("item_desc", sa.Text),
        sa.column("sort_order", sa.Integer),
        sa.column("create_user", sa.String),
    )
    op.execute(
        postgresql.insert(dim)
        .values(
            [
                {
                    "dimension_code": d,
                    "item_code": code,
                    "item_label": label,
                    "item_desc": desc,
                    "sort_order": order,
                    "create_user": "system",
                }
                for d, code, label, desc, order in _DIMENSION_SEED
            ]
        )
        .on_conflict_do_nothing(index_elements=["dimension_code", "item_code"])
    )


def downgrade() -> None:
    """移除兩張新表、還原 6 條索引為非 partial、移除人工欄。

    ⚠️ 不還原 `attribution_tbl` 的表註解文字（純文件性質，還原後反而與 tables.py 不一致）。
    人工糾正資料隨欄位一併消失，不可逆——真要保留請先 pg_dump。
    """
    op.drop_table("attribution_dimension_master")
    op.drop_table("attribution_suggestion_lst")
    op.drop_index("idx_attribution_tbl_mix02", table_name="attribution_tbl")
    for col in _PARTIAL_INDEXED_COLS:
        op.drop_index(f"idx_attribution_tbl_{col}", table_name="attribution_tbl")
        op.create_index(f"idx_attribution_tbl_{col}", "attribution_tbl", [col])
    for col in _ATTR_COLUMN_COMMENTS:
        op.drop_column("attribution_tbl", col)

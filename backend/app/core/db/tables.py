"""資料層 schema 與 engine（SQLAlchemy Core · PostgreSQL only）。

app 操作庫一律 PostgreSQL（對齊 QC DB）；連線取自 `config.env.database_url`
（dev 預設本機 `postgresql+psycopg2://localhost:5432/kkdb_ai_quality`，prod 經 env 覆蓋）。
db 子模組的函式皆走本模組的 engine + Table metadata；schema 演進由 Alembic 管（見 alembic/）。

時間欄位沿用 ISO 字串（Text，與既有 API 回傳形態一致）。

## 字串欄一律 `Text`，只有審計欄用 `String(255)`（刻意，勿再「補上長度」）

DDL 規範的「email 255／短代碼 20~50／長文不限長」長度分級**只套用在審計欄**，其餘 46 個字串欄
維持 `Text`。理由：PG 的 `varchar(n)` 底層與 `text` 同為 varlena，長度上限**不影響儲存 / 索引 /
效能**，唯一效果是一個長度 CHECK——而本表族的字串內容幾乎都不由我們決定：

- LLM 產出：`evidence`（實測已 300 字元，逐字擷取原文，天然無上界）、`recommended_action`、`model`
  （供應商模型名逐代變長）
- 使用者輸入：`upload_batch_tbl.original_name`（實測 121，「檔名::工作表名」全由上傳者決定）、
  `note` / `note_content`
- 上游 production 投影：`evidence_snapshot_tbl` 的 `prod_desc` / `pkg_name` / `supplier_name`
  （dev 快取僅 2 列，樣本不足以推論上界）

超長的下場是寫入時 `StringDataRightTruncation` → HTTP 500，換來的只有一個沒有效能收益的 CHECK。
封閉值域的代碼欄（`polarity` / `conf_tier` / `l1_code` / `l2_code`…）另有一層考量：值域由業務可改的
`config/ai_judge/*.json` 決定，釘死長度等於把改分類體系綁上一次 migration。

審計欄（`create_user` / `modify_user` / `author`）是例外，取 `String(255)`：它的值域**確實**封閉在
email 與 `system:*` 標記，255 對齊 RFC 5321 的 email 位址上限 254。曾短暫收成 36，但當時的最長值
本身就是 36（`system:conversations-30col-migration`）、邊際為零，且寫入端無截斷守衛。

## `setting_master.setting_value` 維持 `Text`（存 JSON 字串），不轉 `jsonb`

jsonb 會把物件的 key 正規化重排（實測本列：`qc_connections, qc_passwords` 會被換成
`qc_passwords, qc_connections`），本專案在設定分組排序上已為此吃過虧。而收益是零——該表
單例一列、永遠整包讀出交給 Python `json.loads` 解析，從不以 JSON path 查詢或索引；欄內還含機密
欄位的 at-rest 密文，語義上就是一團不透明字串。
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    Identity,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as _pg_insert
from sqlalchemy.engine import Engine

from app.core.config import env

metadata = MetaData()

attributions = Table(
    "attribution_tbl",
    metadata,
    Column(
        "attribution_oid", Integer, Identity(), primary_key=True, comment="流水號主鍵（serial）"
    ),
    # ── 來源複合鍵 (source, source_id)：關聯回來源表 ──
    Column(
        "feedback_source_code",
        Text,
        key="source",
        comment="反饋來源 code，決定關聯哪張來源表（reviews / conversations…）",
    ),
    Column(
        "source_id",
        Text,
        comment="該來源表的特徵 id 原值（reviews→rec_oid / conversations→session_oid / "
        "freshdesk_tickets→id / app_feedback→oid / mixpanel_tracker→insert_id）",
    ),
    # ── 傾向 / 階段 ──
    Column("polarity", Text, comment="情緒傾向：positive / negative / neutral"),
    Column(
        "sentiment_score",
        Integer,
        comment="情緒分 1-5（LLM 讀原文判定，與 polarity 同段輸出：負 1-2 / 中 3 / 正 4-5）——"
        "與外部評論 sentiment 同尺度供逐則比對；null＝未初判",
    ),
    Column(
        "prejudge_stage",
        Text,
        comment="初判完成度：judged（已初判）/ pending_review（待複審）/ pending_data（待數據補充）",
    ),
    # ── 歸因分類 L1→L2（code 與中文 label 同存＝SSOT 即資料本身）──
    Column("l1_code", Text, comment="L1 域代碼（如 content / supplier）"),
    # label 與 code 雙射（實測 l1 7↔7↔7、l2 32↔32↔32），看起來冗餘但**刻意保留**：
    # 這是判決當下的分類名快照。改成讀取時由分類體系推導的話，日後改寫措辭會回溯改變
    # 6,242 列歷史歸因的顯示文字——匯出的舊報表與 DB 現值就對不上了。
    Column("l1_label", Text, comment="L1 域中文名（判決當下的快照，非讀取時推導）"),
    Column("l2_code", Text, comment="L2 面向代碼（C-N-M）"),
    Column("l2_label", Text, comment="L2 面向中文名（判決當下的快照，理由同 l1_label）"),
    # ── 信心 ──
    Column("conf_value", Float, comment="最終信心值（校準後，0~1）"),
    Column("conf_raw", Float, comment="arbiter LLM 回報的原始信心值（未校準）"),
    Column(
        "conf_tier",
        Text,
        comment="信心分層：auto_accept（自動採信）/ jury（評審複審）/ needs_review（人工複審）",
    ),
    # ── 初判內容 ──
    Column(
        "summary",
        JSONB,
        comment="反饋摘要（語系→簡明摘要 map，務必含 zh-tw；表格只顯示 zh-tw，逐字原文佐證另存 evidence）",
    ),
    Column("evidence", Text, comment="佐證原文（自反饋原文逐字擷取的片段）"),
    Column("recommended_action", Text, key="action", comment="建議行動"),
    # ── 元數據 ──
    Column("model", Text, comment="產出本列的初判模型；stub 模式為 'stub'"),
    Column("is_primary", Boolean, server_default="false", comment="多歸因中的主歸因旗標"),
    Column(
        "is_auto_accepted",
        Boolean,
        server_default="false",
        comment="系統是否自動採納：信心達 auto_accept 門檻且非 needs_review 階段時由 "
        "prejudge._route_auto_accept 設 true；jury 分層與低信心重路由留 false",
    ),
    Column(
        "create_date",
        DateTime(timezone=True),
        key="created_at",
        comment="初判落庫時間（timestamptz，UTC）＝本列唯一時間源",
    ),
    # (source, source_id) 複合索引：所有歸因查詢的 join / EXISTS 走此複合條件。
    # 單獨的 source 述詞也吃這條（前綴欄），故不另開 source 單欄索引。
    Index("idx_attribution_tbl_mix01", "source", "source_id"),
    # 列表深化篩選熱路徑（typed 欄直接 btree 索引，取代舊 JSONB expression 索引）。
    # 下面這 6 條曾被提議以「低選擇度」為由刪除，實測後**確定保留**：提議的依據是各欄
    # 最高頻值佔比 0.70~0.92，但列表 UI 篩的是**少數值**——conf_tier='needs_review' 佔
    # 2.1%、prejudge_stage='pending_data' 佔 0.08%、polarity='negative' 佔 4.3%，選擇度很高。
    # EXPLAIN ANALYZE 實測 planner 全部採用：等值/IN 篩選走 Index Only Scan（Heap Fetches 0），
    # GROUP BY 聚合也走 Index Only Scan，而 EXISTS 相關子查詢是由
    # `Bitmap Index Scan on idx_attribution_tbl_polarity` 當驅動節點——與「EXISTS 不走
    # index path」的說法相反。本表還會隨初判推進成長約一個數量級（來源表 7.4 萬列僅 8% 已初判），
    # 屆時 Seq Scan 成本線性上升而索引不變。
    #
    # ⚠️ **2026-08-06 起全部改為 partial（`WHERE is_deleted = false`）**：人工誤判 tombstone 上線後
    # 每條讀取路徑都多帶一個 `is_deleted = false` 述詞，而該欄不在索引裡 → planner 必須回堆表驗證，
    # 上面實測到的 Index Only Scan（Heap Fetches 0）會退化成 Index Scan，整段論證失效。partial 化
    # 讓述詞恆真於索引內容，index-only 得以保住，且索引體積不含已刪列。
    # ⚠️ 查詢端述詞必須渲染成 `is_deleted = false`（用 `sa.false()`，**不要 `.is_(False)`**——後者
    # 渲染 `IS false`，PG 的 predicate implication 不保證能推導出等價，索引會靜默失效不報錯）。
    # partial 化後於 dev 庫（6,321 列）實測 planner 行為與上述一致：`conf_tier='needs_review'`
    # 走 Index Only Scan；`polarity='negative'` 走 Bitmap Index Scan；而列表的 EXISTS 相關子查詢
    # 仍由 `Bitmap Index Scan on idx_attribution_tbl_polarity` 當驅動節點（來源表側 Heap Fetches 0）。
    Index("idx_attribution_tbl_polarity", "polarity", postgresql_where=text("is_deleted = false")),
    Index(
        "idx_attribution_tbl_prejudge_stage",
        "prejudge_stage",
        postgresql_where=text("is_deleted = false"),
    ),
    Index("idx_attribution_tbl_l1_code", "l1_code", postgresql_where=text("is_deleted = false")),
    # L2 taxonomy 子樹篩選 + 情緒分篩選熱路徑（原僅 l1 有索引，l2/sentiment 全表掃）
    Index("idx_attribution_tbl_l2_code", "l2_code", postgresql_where=text("is_deleted = false")),
    Index(
        "idx_attribution_tbl_sentiment_score",
        "sentiment_score",
        postgresql_where=text("is_deleted = false"),
    ),
    Index(
        "idx_attribution_tbl_conf_tier", "conf_tier", postgresql_where=text("is_deleted = false")
    ),
    # 「這則反饋是否人工託管」的 EXISTS 熱路徑（決定重新初判走整組替換還是轉待審建議）。
    # partial 使索引只含人工碰過的極少數列（初期個位數），近乎零成本。
    Index(
        "idx_attribution_tbl_mix02",
        "source",
        "source_id",
        # ⚠️ 謂詞必須與 `_shared.human_touched_cond()` **逐條對齊**：partial 索引只在「查詢條件蘊含
        # 索引謂詞」時才會被採用。少列一個 OR 分支不會讓結果變錯，但查詢從此不蘊含謂詞 → 索引
        # 靜默失效退回 seq scan（2026-08-07 加 review_status 時差點漏掉）。
        postgresql_where=text(
            "is_manual_created OR is_human_corrected OR is_deleted OR review_status = 'confirmed'"
        ),
    ),
    # 真正的自然鍵＝(來源, 評論, L1, L2)：一則反饋在同一個 L1/L2 面向上只會有一條歸因。
    #
    # **用 UniqueConstraint + DEFERRABLE 而非裸的 unique Index**（2026-08-07）：
    # 兩條歸因要互換面向時，任何「先改 A 再改 B」的順序都會在中途撞上這組鍵。延後到 commit
    # 才檢查，互換就是單一交易內兩次 UPDATE，不需要塞暫存假值繞路（見 db.corrections.swap_slots）。
    # PG 的 deferrable 唯一性只能掛在**約束**上、掛不到裸索引，故改用 UniqueConstraint；
    # 兩者產生的 `pg_indexes.indexdef` 逐字相同，test_schema_parity 不受影響。
    #
    # 副作用（刻意的）：deferrable 約束不能當 `ON CONFLICT` 的 arbiter，所以對本表做 upsert 會
    # 直接報錯。這正是我們要的——`replace_source_findings` 早就是「整組刪除後重插」而非逐筆
    # upsert（見該函式 docstring），這條約束把原本只寫在註解裡的約定變成 PG 強制。
    UniqueConstraint(
        "source",
        "source_id",
        "l1_code",
        "l2_code",
        name="idx_attribution_tbl_unique01",
        deferrable=True,
        initially="IMMEDIATE",
    ),
    Column(
        "create_user", String(255), comment="建立者（SSO 接入前一律 system，接入後為使用者 email）"
    ),
    Column(
        "modify_user",
        String(255),
        comment="最後修改者（SSO 接入前一律 system，接入後為使用者 email；NULL＝從未修改）",
    ),
    Column("modify_date", DateTime(timezone=True), comment="最後修改時間"),
    # ── 人工介入欄（2026-08-06 新增）──────────────────────────────────────────
    # ⚠️ **刻意宣告在審計欄之後**（而非與內容欄同區）：PG 的 ADD COLUMN 一律追加到最後，
    # 只有「宣告序＝追加序」時 test_schema_parity 的 column_order 比對才會過。為了美觀把它們
    # 插到中間，就得為 6 千列的表做一次全表重建（見 f1b78d3a95c2），成本效益不成立。
    Column(
        "is_manual_created",
        Boolean,
        nullable=False,
        server_default="false",
        comment="人工手動新增的歸因（AI 未產出、由人補上）",
    ),
    Column(
        "is_human_corrected",
        Boolean,
        nullable=False,
        server_default="false",
        comment="AI 產出後由人工改過值（分類／傾向／情緒分）",
    ),
    Column(
        "is_deleted",
        Boolean,
        nullable=False,
        server_default="false",
        comment="人工標記為 AI 誤判（tombstone）：所有讀取路徑排除，但**保留列以佔住自然鍵**——"
        "使「重新初判把人工刪掉的歸因悄悄復活」在物理上不可能。還原走 restore",
    ),
    Column(
        "correction_reason",
        Text,
        comment="最近一次人工糾正／刪除的理由（提交時強制必填）；歷次理由完整保存於 "
        "attribution_event_lst 的 kind='correction' 事件",
    ),
    Column(
        "review_status",
        Text,
        nullable=False,
        server_default="unreviewed",
        comment="人工複審狀態：unreviewed（未複審）/ confirmed（人工確認 AI 判對）/ "
        "corrected（人工已糾正）——補上 pending_review 進來後沒有出口的缺口",
    ),
    comment="初判歸因結果（一列＝一條歸因，同一則反饋可有多列）。全 typed scalar 欄無 JSONB blob——"
    "本表是查詢／聚合／篩選密集的分析核心，typed 欄可直接 btree 索引且 SQL 乾淨；巢狀物件屬呈現層，"
    "在 API DTO（_shared.attribution_dto）才組。"
    "一則反饋有兩種託管狀態：**AI 託管**（無任何人工痕跡）重新初判即整組替換；**人工託管**"
    "（任一列 is_manual_created / is_human_corrected / is_deleted）重新初判完全不碰本表，"
    "LLM 結果轉入 attribution_suggestion_lst 待人工採納",
)

# ── 5 反饋來源獨立實體表（各自對齊源表 schema，PK=特徵 id；欄位存原始源值 raw text）─────
# 統一經 source_registry（table + natural_key）+ config/ai_judge/source_mapping.json（源欄→canonical）
# 產出顯示層 canonical 欄（content/score/occurred_at…）。欄位一律 Text（忠實 raw；巢狀 JSON 於
# _enrich 端解析，如 reviews.order_snap_json → prod_name）。
# 欄序依 BQ 取數 SQL 的 27 欄輸出契約分組排列（雙主鍵 → 評論／訂單／商品／供應商），
# 與 conversations 的分組風格一致，便於逐欄對照匯入檔。
reviews = Table(
    "review_tbl",
    metadata,
    Column("rec_oid", Text, primary_key=True),  # 特徵 id
    Column(
        "review_external_lst_oid", Text
    ),  # 外部評論號（評論系統 rec_oid 對橋回查鍵；無對應為 NULL）
    # ── 評論資訊 ─────────────────────────────────────────────
    Column("create_date", Text),  # canonical occurred_at
    Column("rec_title", Text),  # canonical title
    Column("rec_desc", Text),  # canonical content（初判主輸入）
    Column("rec_scores", Text),  # canonical score
    Column("traveller_type", Text),
    Column("lang_code", Text),  # canonical lang（評論語系，非訂單語系）
    Column("sentiment", Text),  # 外部 LLM 情緒分 1-5（輔助訊號·傾向以原文判定為準）
    Column(
        "free_tag", Text
    ),  # 外部 LLM 面向標籤 JSON 字串 [{tag_name,tag_value,tag_list}]（輔助訊號）
    Column("member_uuid", Text),
    # ── 訂單資訊 ─────────────────────────────────────────────
    Column("order_oid", Text),
    Column("order_mid", Text),  # ⚠️ 會員 id（個資）
    Column("order_create_time", Text),
    Column("order_lang", Text),
    Column("go_date", Text),  # canonical go_date（出發日；BQ 端已 DATE 轉型）
    Column("order_price", Text),
    Column("order_profit", Text),
    Column("order_create_source_code", Text),
    # ── 商品資訊 ─────────────────────────────────────────────
    Column("prod_oid", Text),
    Column("pkg_oid", Text),
    Column("product_name", Text),
    Column("order_snap_json", Text),  # 多語商品名快照 JSON（enrich 解析 prod_name/package_name）
    Column("bd_tag_cd", Text),  # BD 分工代碼（商品垂直分類篩選鍵，見 bd_tag_vertical）
    Column("bd_tag", Text),  # BD tag 中文
    # ── 供應商資訊 ───────────────────────────────────────────
    Column("supplier_oid", Text),
    Column("supplier_name", Text),  # canonical supplier_name
    Index("idx_review_tbl_create_date", "create_date"),
    Index("idx_review_tbl_prod_oid", "prod_oid"),
    comment="商品評論來源鏡像（忠實鏡射上游 BQ 取數輸出：欄名與型別逐欄對齊，不做 canonical 化）。一列＝一則評論，PK rec_oid",
)

conversations = Table(
    "conversation_tbl",
    metadata,
    Column("session_oid", Text, primary_key=True),  # 特徵 id
    Column(
        "bucket", Text
    ),  # 分桶字面值（BQ 端預算）：transferred/chatbot_only/human_supplier/human_kkday/human_other
    Column("inbound_time", Text),  # canonical occurred_at
    Column("trip_stage", Text),  # canonical trip_stage
    Column("godate_diff", Text),  # 出發日差字面值（BQ 端預算，整數字串）
    Column("msg_handler_bucket", Text),  # 處理方字面值：KKDAY/SUPPLIER
    Column("member_uuid", Text),  # ⚠️ 會員 id（個資）
    Column("order_oid", Text),  # canonical order_oid
    Column("order_mid", Text),
    Column("order_create_time", Text),
    Column("order_status_now", Text),
    Column("order_lang", Text),
    Column("go_date", Text),  # canonical go_date（出發日）
    Column("order_price", Text),
    Column("order_profit", Text),
    Column("order_create_source_code", Text),
    Column("prod_oid", Text),  # canonical prod_oid
    Column("product_name", Text),
    Column("product_tz", Text),
    Column("vertical", Text),  # 商品垂直分類字面值（BQ 端預算）
    Column("bd_tag_cd", Text),
    Column("bd_tag", Text),
    Column(
        "PM", Text
    ),  # 大寫欄名逐字對齊 CSV 表頭（SQLAlchemy 自動加引號保留大小寫，勿手滑小寫化）
    Column("supplier_oid", Text),  # canonical supplier_oid
    Column("supplier_name", Text),  # canonical supplier_name
    Column("cs_tag_oid", Text),
    Column("cs_tag_name", Text),
    Column("user_message_count", Text),
    Column("conversation_full", Text),  # canonical content（初判主輸入）
    Index("idx_conversation_tbl_inbound_time", "inbound_time"),
    Index("idx_conversation_tbl_prod_oid", "prod_oid"),
    Index("idx_conversation_tbl_bucket", "bucket"),
    Index("idx_conversation_tbl_vertical", "vertical"),
    comment="售前售後進線來源鏡像（忠實鏡射上游 BQ 取數輸出）。一列＝一個 IM session，PK session_oid",
)

freshdesk_tickets = Table(
    "freshdesk_ticket_tbl",
    metadata,
    Column("id", Text, primary_key=True),  # 特徵 id
    Column("display_id", Text),
    Column("ticket_type", Text),
    Column("subject", Text),  # canonical title
    Column("description", Text),  # canonical content
    Column("notes", Text),
    Column("attachments", Text),
    Column("st_survey_rating", Text),  # canonical score
    Column("product_id", Text),  # canonical prod_oid
    Column("custom_field", Text),
    Column("tags", Text),
    Column("status_name", Text),
    Column("priority_name", Text),
    Column("source_name", Text),  # canonical channel
    Column("created_at", Text),  # canonical occurred_at
    Column("updated_at", Text),
    Column("requester_id", Text),
    Column("parent_ticket_id", Text),
    Index("idx_freshdesk_ticket_tbl_created_at", "created_at"),
    Index("idx_freshdesk_ticket_tbl_product_id", "product_id"),
    comment="Freshdesk 工單來源鏡像（忠實鏡射上游取數輸出）。一列＝一張工單，PK id",
)

app_feedback = Table(
    "app_feedback_tbl",
    metadata,
    Column("oid", Text, primary_key=True),  # 特徵 id
    Column("created_datetime", Text),  # canonical occurred_at
    Column("comment", Text),  # canonical content
    Column("score", Text),  # canonical score
    Column("source", Text),  # 來源渠道（app 端，與 attributions.source 不同語意）
    Column("lang_code", Text),  # canonical lang
    Column("version", Text),
    Index("idx_app_feedback_tbl_created", "created_datetime"),
    comment="App 內回饋來源鏡像（忠實鏡射上游取數輸出）。一列＝一則回饋，PK oid",
)

mixpanel_tracker = Table(
    "mixpanel_tracker_tbl",
    metadata,
    Column("insert_id", Text, primary_key=True),  # 特徵 id（源 $insert_id 淨化）
    Column("event", Text),  # canonical channel
    Column("time", Text),  # canonical occurred_at
    Column("distinct_id", Text),  # 源 $distinct_id 淨化
    Column("feedback_signal", Text),
    Column("negative_items", Text),  # canonical content
    Column("display_style", Text),
    Column("order_mid", Text),
    Column("order_status", Text),
    Column("order_master_mid", Text),
    Column("is_marketplace", Text),
    Column("prod_mid", Text),  # canonical prod_oid
    Column("pkg_oid", Text),
    Column("prod_city_code", Text),
    Column("prod_country_code", Text),
    Column("prod_info", Text),
    Column("bd_tag", Text),
    Column("msg_handler", Text),
    Column("current_url", Text),  # 源 $current_url 淨化
    Column("platform", Text),  # 源 Platform 淨化
    Column("mp_country_code", Text),
    Column("os", Text),  # 源 $os 淨化
    Index("idx_mixpanel_tracker_tbl_time", "time"),
    comment="Mixpanel 埋點回饋來源鏡像（忠實鏡射上游取數輸出）。一列＝一個事件，PK insert_id",
)

batches = Table(
    "upload_batch_tbl",
    metadata,
    Column(
        "upload_batch_oid", Integer, Identity(), primary_key=True, comment="流水號主鍵（serial）"
    ),
    Column("batch_id", Text, nullable=False, comment="上傳批次 id（uuid hex）"),
    Column(
        "batch_name", Text, key="name", comment="自動命名的批次名「{來源} YYYYMMDD{當天序號:02d}」"
    ),
    Column(
        "feedback_source_code",
        Text,
        key="source",
        comment="反饋來源 code（reviews / conversations…）",
    ),
    Column("original_name", Text, comment="上傳檔名（多分頁 xlsx 為「檔名::工作表名」）"),
    Column("row_count", Integer, comment="該工作表的資料列數"),
    Column(
        "create_date",
        DateTime(timezone=True),
        key="uploaded_at",
        comment="上傳時間（timestamptz，UTC）",
    ),
    Column("note", Text, comment="使用者上傳時輸入的備註（每工作表一則，隨批次保存）"),
    Index("idx_upload_batch_tbl_unique01", "batch_id", unique=True),
    Column(
        "create_user", String(255), comment="建立者（SSO 接入前一律 system，接入後為使用者 email）"
    ),
    Column(
        "modify_user",
        String(255),
        comment="最後修改者（SSO 接入前一律 system，接入後為使用者 email；NULL＝從未修改）",
    ),
    Column("modify_date", DateTime(timezone=True), comment="最後修改時間"),
    comment="上傳批次審計流水：一列＝一次上傳中的一張工作表，供資料上傳頁回溯來源檔與筆數",
)

settings = Table(
    "setting_master",
    metadata,
    Column("setting_oid", Integer, Identity(), primary_key=True, comment="流水號主鍵（serial）"),
    Column(
        "setting_code", Text, nullable=False, key="key", comment="設定鍵；目前僅單例 '__global__'"
    ),
    Column(
        "setting_value",
        Text,
        key="data",
        comment="設定全文 JSON 字串；機密欄位（token/密碼）為 at-rest 密文",
    ),
    Column(
        "modify_date",
        DateTime(timezone=True),
        key="updated_at",
        comment="最後更新時間（timestamptz，UTC）",
    ),
    Index("idx_setting_master_unique01", "key", unique=True),
    Column(
        "create_user", String(255), comment="建立者（SSO 接入前一律 system，接入後為使用者 email）"
    ),
    Column("create_date", DateTime(timezone=True), comment="建立時間"),
    Column(
        "modify_user",
        String(255),
        comment="最後修改者（SSO 接入前一律 system，接入後為使用者 email；NULL＝從未修改）",
    ),
    comment="全項目共享設定（單例 row，見 core/settings.py）：LLM 連線與模型配置庫、QC DB 連線、"
    "功能區綁定、導出偏好。機密欄位加密後才落此表",
)

judge_rule_versions = Table(
    "judge_rule_version_lst",
    metadata,
    Column(
        "judge_rule_version_oid",
        Integer,
        Identity(),
        primary_key=True,
        key="id",
        comment="流水號主鍵（serial）",
    ),
    Column(
        "rule_code",
        Text,
        nullable=False,
        comment="規則代碼：bd_tag_vertical / source_mapping / prompt_polarity / prompt_C-1~C-6",
    ),
    Column(
        "version_number",
        Integer,
        nullable=False,
        key="version",
        comment="版本號，per rule_code 從 1 遞增",
    ),
    Column(
        "rule_content",
        JSONB,
        nullable=False,
        key="content",
        comment="該版本完整內容；prompt_* 為 {_meta, text(md 全文)}",
    ),
    Column("note", Text, comment="存檔備註（使用者輸入，說明本次改了什麼）"),
    Column(
        "create_user",
        String(255),
        key="author",
        comment="存檔人（SSO 接入前一律 system，接入後為使用者 email）",
    ),
    Column(
        "is_active",
        Boolean,
        nullable=False,
        server_default="false",
        comment="是否為線上生效版；一 rule_code 僅一筆為 true（由部分唯一索引強制）",
    ),
    Column(
        "create_date",
        DateTime(timezone=True),
        server_default=func.now(),
        key="created_at",
        comment="存檔時間",
    ),
    UniqueConstraint("rule_code", "version", name="idx_judge_rule_version_lst_unique01"),
    # 一 rule_code 僅一 active（部分唯一索引）
    Index(
        "idx_judge_rule_version_lst_unique02",
        "rule_code",
        unique=True,
        postgresql_where=text("is_active"),
    ),
    comment="初判規則版本庫（append-only 快照：每次存檔 insert 新列不就地改，規避 JSONB "
    "write-amplification）。檔案 config/*.json 與 prompts/*.md 為默認 seed，本表存 live + 完整歷史",
)


llm_usage = Table(
    "llm_usage_lst",
    metadata,
    Column(
        "llm_usage_oid",
        Integer,
        Identity(),
        primary_key=True,
        key="id",
        comment="流水號主鍵（serial）",
    ),
    Column(
        "stage",
        Text,
        comment=(
            "呼叫階段／呼叫者：polarity / C-1~C-6 / attribute / pack_* / prompt_debug / "
            "prompt_debug_batch / prompt_revise…（非反饋來源驅動的呼叫，歸屬由此欄表達）"
        ),
    ),
    Column("model", Text, nullable=False, comment="實際使用的模型（cfg.model）"),
    Column("prompt_tokens", Integer, comment="輸入 token 數"),
    Column(
        "completion_tokens",
        Integer,
        comment="輸出 token 數（reasoning model 下已含 reasoning_tokens）",
    ),
    Column(
        "reasoning_tokens",
        Integer,
        comment="completion 中屬 reasoning 的部分（reasoning_effort 產出；供量測降檔位的空間）",
    ),
    Column("cached_tokens", Integer, comment="prompt 中命中 prompt cache 的 token 數（折扣計價）"),
    Column(
        "cost_usd",
        Float,
        comment="本次呼叫費用（pricing.cost_usd 換算，含 cache 折扣與 service tier）",
    ),
    Column(
        "feedback_source_code",
        Text,
        key="source",
        comment=(
            "反饋來源 code（reviews / conversations / freshdesk_tickets / app_feedback / "
            "mixpanel_tracker）；非反饋來源驅動的呼叫（調試台、AI 改寫）為空"
        ),
    ),
    Column("job_id", Text, comment="所屬批次任務 id；單次呼叫為空"),
    Column(
        "create_date",
        DateTime(timezone=True),
        server_default=func.now(),
        key="created_at",
        comment="呼叫時間",
    ),
    Index("idx_llm_usage_lst_create_date", "created_at"),
    Index("idx_llm_usage_lst_model", "model"),
    Index("idx_llm_usage_lst_stage", "stage"),
    Column(
        "create_user", String(255), comment="建立者（SSO 接入前一律 system，接入後為使用者 email）"
    ),
    comment="AI 使用紀錄（per-call：每次真實 LLM 呼叫落一列），供成本 dashboard 多維度聚合。"
    "唯一寫入點＝llm.client 的 usage recorder（批次走 buffer 批量寫、單次即時寫）",
)

prejudge_runs = Table(
    "prejudge_run_tbl",
    metadata,
    Column(
        "prejudge_run_oid", Integer, Identity(), primary_key=True, comment="流水號主鍵（serial）"
    ),
    Column(
        "job_id",
        Text,
        nullable=False,
        comment="批次任務 id（pj_* uuid；與 llm_usage.job_id 對齊）。"
        "PK 已改為 serial prejudge_run_oid，本欄降為 UNIQUE 業務鍵——"
        "它同時是 in-mem registry 的 key 與 SSE 端點的 capability token，語義不可變",
    ),
    Column(
        "trigger_kind",
        Text,
        nullable=False,
        key="kind",
        comment="觸發型態：batch（依條件批量選取）/ selected（勾選多筆）/ single（單筆）",
    ),
    Column(
        "is_rejudge",
        Boolean,
        key="rejudge",
        comment="標的先前已有初判結果 → 本次為重新初判",
    ),
    Column("feedback_source_code", Text, key="source", comment="反饋來源 code（reviews…）"),
    Column("model", Text, comment="本次使用的初判模型"),
    Column(
        "params",
        JSONB,
        comment="發起參數快照（stages/verticals/傾向/信心上限…；item_ids 只留樣本避免膨脹）",
    ),
    Column(
        "run_status",
        Text,
        nullable=False,
        key="status",
        comment="running / paused / cancelling → 終態 done / error / cancelled / interrupted（行程重啟）",
    ),
    Column("total", Integer, comment="本次標的總筆數"),
    Column("processed", Integer, comment="已處理筆數（終態回寫；執行中由 in-mem 快照 overlay）"),
    Column("ok", Integer, comment="成功筆數"),
    Column("failed", Integer, comment="失敗筆數"),
    Column("total_tokens", BigInteger, comment="本 run 累計 token（usage sink 加總）"),
    Column("cost_usd", Float, comment="本 run 累計費用（pricing 換算）"),
    Column(
        "create_user",
        String(255),
        key="triggered_by",
        comment="觸發人（SSO 接入前一律 system，接入後為使用者 email）",
    ),
    Column(
        "create_date",
        DateTime(timezone=True),
        server_default=func.now(),
        key="started_at",
        comment="開始時間",
    ),
    Column("finished_at", DateTime(timezone=True), comment="結束時間；執行中為空"),
    Index("idx_prejudge_run_tbl_create_date", "started_at"),
    Index("idx_prejudge_run_tbl_unique01", "job_id", unique=True),
    Column(
        "modify_user",
        String(255),
        comment="最後修改者（SSO 接入前一律 system，接入後為使用者 email；NULL＝從未修改）",
    ),
    Column("modify_date", DateTime(timezone=True), comment="最後修改時間"),
    comment="初判批次執行紀錄（run 級：每次觸發初判的動作落一列）。與 llm_usage（call 級）以 job_id "
    "關聯——本表存業務語境（誰／何時／範圍／參數／結果統計），token 與費用明細由 llm_usage 聚合",
)

prejudge_run_logs = Table(
    "prejudge_run_log_lst",
    metadata,
    Column(
        "prejudge_run_log_oid",
        Integer,
        Identity(),
        primary_key=True,
        comment="流水號主鍵（serial）",
    ),
    Column(
        "job_id",
        Text,
        nullable=False,
        comment="所屬初判任務 id（與 prejudge_runs.job_id 對齊；無 FK，軟關聯）",
    ),
    Column(
        "source_id",
        Text,
        nullable=False,
        comment="日誌所屬評論的來源自然鍵；空字串＝job 級事件（任務啟動/收尾，不屬於任何單一評論）",
    ),
    Column(
        "entries",
        JSONB,
        nullable=False,
        comment="該評論本次初判的完整日誌條目陣列（run_log entries 形狀：ts/kind/stage/message/label/data）",
    ),
    # 本表不出 wire（讀取端只回 entries 陣列），故欄名不做 Python key 別名，DB 規範名直用
    Column(
        "create_user", String(255), comment="觸發人（SSO 接入前一律 system，接入後為使用者 email）"
    ),
    Column(
        "create_date",
        DateTime(timezone=True),
        server_default=func.now(),
        comment="落庫時間（該筆判完即寫，非整批結束才寫）",
    ),
    # 回看熱路徑＝(job_id, source_id) 直取單筆；唯一性同時保證重跑同一筆時 upsert 而非長出重複列
    Index("idx_prejudge_run_log_lst_unique01", "job_id", "source_id", unique=True),
    Index("idx_prejudge_run_log_lst_create_date", "create_date"),
    comment="初判執行日誌（append-only 明細流水，一則評論一列）：每筆判完即落庫，記憶體佔用與批量大小"
    "脫鉤，故不分批量大小全數收集。刻意不與 prejudge_runs 同列——日誌是數十 KB 的 blob，若塞回 run 列"
    "的 JSONB 欄，逐筆累加會讓 Postgres 每次都整列重寫（O(N²) 寫入放大）",
)


attribution_history = Table(
    "attribution_event_lst",
    metadata,
    Column(
        "attribution_event_oid",
        Integer,
        Identity(),
        primary_key=True,
        key="id",
        comment="流水號主鍵（serial）",
    ),
    Column(
        "feedback_source_code",
        Text,
        nullable=False,
        key="source",
        comment="反饋來源 code（reviews…）",
    ),
    Column("source_id", Text, nullable=False, comment="該來源的特徵 id（評論級鍵）"),
    Column(
        "kind",
        Text,
        nullable=False,
        comment="事件類型：prejudge（初判快照）/ failure（初判失敗留痕）/ correction（人工糾正）/ "
        "review_confirm（複審確認）/ suggestion（重判轉待審建議）/ "
        "suggestion_resolved（建議採納或駁回）/ "
        "router_shadow（域路由召回量測，內部遙測不進使用者時間軸）。"
        "備註不在此表——見 attribution_note_lst（活查詢鍵不埋 JSONB）",
    ),
    Column("model", Text, comment="初判模型（kind=prejudge）"),
    Column(
        "params",
        JSONB,
        comment="事件細節：prejudge 存 {model,…}（回填列為 {backfilled:true}）；failure 存 {error}；"
        "router_shadow 存 {candidates,hit,missed,probs}",
    ),
    Column(
        "attribution_snapshot",
        JSONB,
        key="attributions",
        comment="初判結果快照（attribution_dto 形狀陣列；僅 kind=prejudge 有值）",
    ),
    Column(
        "result_digest", Text, comment="快照全欄位（排除時間戳）正規化後的 sha256，供相鄰列去重比對"
    ),
    Column(
        "job_id", Text, comment="觸發的批次任務 id（與 prejudge_runs.job_id 對齊）；人工事件為空"
    ),
    Column(
        "create_user",
        String(255),
        key="triggered_by",
        comment="觸發人（SSO 接入前一律 system，接入後為使用者 email；kind=prejudge）",
    ),
    Column(
        "author",
        String(255),
        comment="備註人（SSO 接入前一律 system，接入後為使用者 email；kind=note）",
    ),
    Column("note_content", Text, key="content", comment="備註內容（kind=note）"),
    Column(
        "create_date",
        DateTime(timezone=True),
        server_default=func.now(),
        key="created_at",
        comment="事件時間",
    ),
    # 評論時間軸查詢熱路徑：(source, source_id) 定位 + created_at 排序
    Index("idx_attribution_event_lst_mix01", "source", "source_id", "created_at"),
    Index("idx_attribution_event_lst_create_date", "created_at"),
    # latest_snapshots（DISTINCT ON）快照查詢熱路徑 partial index——登記於 metadata 使
    # create_all（空庫路徑）與 migration（既有庫路徑）產出一致，消弭兩路徑 schema drift
    Index(
        "idx_attribution_event_lst_mix02",
        "source",
        "model",
        "source_id",
        text("create_date DESC"),
        postgresql_where=text("kind = 'prejudge'"),
    ),
    comment="評論級歸因歷史（append-only 事件流）：補 attributions「刪+插」重新初判不留痕的缺口——"
    "prejudge_runs 是 run 級、llm_usage 是 call 級，皆無法重建單一評論的初判演進。"
    "無 FK，以 (source, source_id) 邏輯鍵關聯（該鍵跨重新初判穩定；歸因列本身會整批換掉）",
)


attribution_suggestions = Table(
    "attribution_suggestion_lst",
    metadata,
    Column(
        "attribution_suggestion_oid",
        Integer,
        Identity(),
        primary_key=True,
        comment="流水號主鍵（serial）",
    ),
    # 本表刻意**不用別名欄**（不寫 key=）：`test_wire_contract` 從 metadata 自動派生「含別名欄的表」
    # 清單並要求每張都登記 wire 探針，而本表在待審建議 API 落地前沒有任何端點會直出它。
    # 待 PR3 接上端點時再依實際 wire 形狀決定是否要別名。
    Column("feedback_source_code", Text, nullable=False, comment="反饋來源 code（reviews…）"),
    Column("source_id", Text, nullable=False, comment="該來源的特徵 id（評論級鍵）"),
    Column(
        "suggestion_batch_id",
        Text,
        nullable=False,
        comment="一次重新初判對一則反饋產生的一組建議（整組採納／事件關聯用）",
    ),
    Column(
        "change_type",
        Text,
        nullable=False,
        comment="變更型別：replace（同 L1/L2 面向但值有異）/ add（AI 新發現的面向）/ "
        "remove（AI 認為現值面向不再成立）",
    ),
    Column(
        "attribution_oid",
        Integer,
        comment="對應的現值列（replace / remove 有值；add 為空）。軟關聯無 FK",
    ),
    # ── LLM 建議值（欄型與 attribution_tbl 對應欄一致；remove 型取自現值，供 UI 顯示要移除什麼）──
    Column("polarity", Text, comment="建議的情緒傾向"),
    Column("sentiment_score", Integer, comment="建議的情緒分 1-5"),
    Column("l1_code", Text, comment="建議的 L1 域代碼"),
    Column("l1_label", Text, comment="建議的 L1 域中文名（產生當下的快照）"),
    Column("l2_code", Text, comment="建議的 L2 面向代碼"),
    Column("l2_label", Text, comment="建議的 L2 面向中文名（產生當下的快照）"),
    Column("conf_value", Float, comment="建議的信心值（校準後）"),
    Column("conf_raw", Float, comment="建議的原始信心值"),
    Column("conf_tier", Text, comment="建議的信心分層"),
    Column("summary", JSONB, comment="建議的反饋摘要（語系→摘要 map）"),
    Column("evidence", Text, comment="建議的佐證原文"),
    Column("recommended_action", Text, comment="建議的建議行動"),
    Column("model", Text, comment="產生本建議的初判模型"),
    Column("job_id", Text, comment="產生本建議的初判任務 id（與 prejudge_runs.job_id 對齊）"),
    Column("create_user", String(255), comment="觸發本次重新初判的人"),
    Column(
        "create_date",
        DateTime(timezone=True),
        server_default=func.now(),
        comment="建議產生時間",
    ),
    # 索引宣告在全部 Column 之後：inline Index 以欄名引用時，該欄必須先出現於 Table args
    # 列表徽記與抽屜取數熱路徑
    Index("idx_attribution_suggestion_lst_mix01", "feedback_source_code", "source_id"),
    # 批內去重：使同一次重新初判重跑時的寫入冪等
    Index(
        "idx_attribution_suggestion_lst_unique01",
        "suggestion_batch_id",
        "l1_code",
        "l2_code",
        unique=True,
    ),
    Index("idx_attribution_suggestion_lst_create_date", "create_date"),
    comment="待審 LLM 建議（人工託管的反饋重新初判時，AI 結果不寫 attribution_tbl 而轉入本表）。"
    "本表語義是「**當前尚未處理**的建議」：採納／駁回即刪除該列（決策本身記在 attribution_event_lst "
    "的 kind='suggestion_resolved' 事件），再次重新初判即先清光舊 pending 再插新的——故刻意"
    "沒有 review_status 狀態機",
)


attribution_dimensions = Table(
    "attribution_dimension_master",
    metadata,
    Column(
        "attribution_dimension_oid",
        Integer,
        Identity(),
        primary_key=True,
        comment="流水號主鍵（serial）",
    ),
    Column(
        "dimension_code",
        Text,
        nullable=False,
        comment="值域維度：responsible_party（責任方）/ severity（嚴重度）/ "
        "verdict_action（建議行動）",
    ),
    Column(
        "item_code",
        Text,
        nullable=False,
        comment="項目機器碼（落入判決欄的值；改碼＝改歷史語義，禁改）",
    ),
    Column(
        "item_label",
        Text,
        nullable=False,
        comment="項目中文名（顯示用可改；判決落庫時同存快照，改名不回溯污染歷史判決）",
    ),
    Column("item_desc", Text, comment="判準說明（給定責的人看的口徑描述）"),
    Column(
        "sort_order",
        Integer,
        nullable=False,
        server_default="0",
        comment="同維度內顯示排序（小在前）",
    ),
    Column(
        "is_active",
        Boolean,
        nullable=False,
        server_default="true",
        comment="是否可選（停用不刪——硬刪已被歷史判決引用的 code 會讓那些列顯示空白）",
    ),
    Column("create_user", String(255), comment="建立者"),
    Column("create_date", DateTime(timezone=True), server_default=func.now(), comment="建立時間"),
    Column("modify_user", String(255), comment="最後修改者"),
    Column("modify_date", DateTime(timezone=True), comment="最後修改時間"),
    Index("idx_attribution_dimension_master_unique01", "dimension_code", "item_code", unique=True),
    Index("idx_attribution_dimension_master_mix01", "dimension_code", "sort_order"),
    comment="判決歸因值域主檔（責任方／嚴重度／建議行動三軸共用一表，以 dimension_code 判別）。"
    "三者欄形完全相同，拆三張表＝三套 migration／API／畫面；判別式單表是既有慣例"
    "（judge_rule_version_lst 用 rule_code 判別）。檔案 config/ai_judge/attribution_dimension.json "
    "為默認 seed，本表存 live",
)


# ── 反饋備註（人在時間軸上留的處理脈絡；反饋級或面向級）────────────────────────────
attribution_notes = Table(
    "attribution_note_lst",
    metadata,
    Column(
        "attribution_note_oid",
        Integer,
        Identity(),
        primary_key=True,
        comment="流水號主鍵（serial）",
    ),
    Column(
        "feedback_source_code",
        Text,
        key="source",
        nullable=False,
        comment="反饋來源 code（reviews…）",
    ),
    Column("source_id", Text, nullable=False, comment="該來源的特徵 id（反饋級鍵）"),
    Column(
        "l1_code",
        Text,
        comment="歸因域機器碼；與 l2_code 皆 NULL＝整則備註，皆有值＝面向備註。"
        "**綁面向而非 attribution_oid**：歸因列每次重新初判都整批換掉（先刪後插），綁流水號的"
        "東西一重判就成孤兒——2026-08-04 退役的 finding_notes 表 8 列裡有 6 列正是這樣死的。"
        "面向鍵跨重判穩定，而且即使該面向之後消失，備註本身仍自我描述（讀得懂當初在講什麼）",
    ),
    Column("l2_code", Text, comment="歸因面向機器碼（同 l1_code 的說明）"),
    Column(
        "note_type",
        Text,
        nullable=False,
        comment="互動類型機器碼；值域＝attribution_dimension_master 的 note_type 軸（業務可維護）",
    ),
    Column("content", Text, nullable=False, comment="備註內容"),
    Column("create_user", String(255), comment="留下這則備註的人（無 SSO 時為 system）"),
    Column("create_date", DateTime(timezone=True), server_default=func.now(), comment="建立時間"),
    # **刻意不宣告 modify_user / modify_date**：本表 append-only，「只進不改」寫死在 schema 上
    # 而不是靠慣例。備註是互動軌跡，寫出去之後若能改成別的，這條軌跡的稽核價值就沒了。
    # ⚠️ Index 的字串以 **Python key** 解析（`source` 而非 DB 欄名 `feedback_source_code`）——
    # 帶 `key=` 別名的欄用 DB 名會拋 ConstraintColumnNotFoundError。
    Index("idx_attribution_note_lst_mix01", "source", "source_id", "create_date"),
    comment="反饋備註（append-only，不剪枝）。l1_code/l2_code 皆 NULL＝整則備註、皆有值＝面向備註。"
    "**不與 attribution_event_lst 共用**：那張表的 params 承載的是「事件當下發生了什麼」的凍結"
    "快照（寫完就不再查詢的死指標），而備註的面向鍵是活的查詢鍵（每次開工作台都要按面向撈、"
    "列表要算數量）——活查詢鍵埋進凍結 JSONB 就永遠無法建索引、無法對 note_type 做參照約束",
)


# ── 訂單佐證快照（qc_evidence 快取的 PG 儲存層：下單當時投影快照，一訂單一列 + TTL）──────
# runtime 派生快取（真相源＝production snapshot，可重生）。⚠️ 刻意不入 datapack
# TABLE_LOAD_ORDER：快取不隨資料包匯出/匯入（含 PII-adjacent 商品內容，且匯入端重抓即可）。
# TTL 過期由 qc_evidence 讀寫路徑懶清理（讀到過期＝miss 並刪列；寫入時順手清全表過期列，
# 走 expires_at 索引）。**欄位徹底拆開**（非單一 payload jsonb）：ID/純量各自成欄方便 grid
# 瀏覽與篩選，商品/規格/方案內容各自獨立 jsonb 欄（每欄鏡射一段來源 SELECT 投影，欄名/結構
# 不改名重組——查詢層 qc_evidence._fetch_full_snapshot 逐欄核對）；欄名直接帶群組前綴（供
# grid 瀏覽即知歸屬），API 讀出後在 qc_evidence 組裝成樹狀分組物件（order_summary/
# supplier_info/product_info/item_info/package_info/meta）供前端顯示。
evidence_snapshot = Table(
    "evidence_snapshot_tbl",
    metadata,
    Column(
        "evidence_snapshot_oid",
        Integer,
        Identity(),
        primary_key=True,
        comment="流水號主鍵（serial）",
    ),
    Column("order_oid", BigInteger, nullable=False, comment="訂單 oid（一訂單一列）"),
    # ── order_summary 群組（來源 order_tbl）──
    Column("order_mid", Text, comment="訂單編號（對外顯示用的訂單號）"),
    Column("order_status", Text, comment="下單當時的訂單狀態"),
    Column("price_pay", Float, comment="實付金額"),
    Column("lang_code", Text, comment="訂單語系"),
    Column("crt_dt", DateTime(timezone=True), comment="訂單建立時間"),
    # ── order_summary 群組（來源 order_lst）──
    Column("prod_oid", BigInteger, comment="商品 oid"),
    Column("prod_version", BigInteger, comment="下單當時的商品版本號（佐證取的是這一版內容）"),
    Column("pkg_oid", BigInteger, comment="方案 oid（來源欄 prod_level2_oid）"),
    Column("item_oid", BigInteger, comment="規格 oid"),
    Column("supplier_oid", BigInteger, comment="供應商 oid"),
    Column("lst_dt_go", DateTime(timezone=True), comment="出發／使用日期"),
    Column("product_timezone", Text, key="timezone", comment="商品所在時區"),
    Column("pkg_name", Text, comment="方案名稱（來源欄 prod_level2_name）"),
    Column("prod_desc", Text, comment="商品名稱／描述"),
    # ── supplier_info 群組 ──
    Column("supplier_name", Text, comment="供應商名稱"),
    Column("supplier_order_handler", Text, comment="訂單處理方（KKDAY / SUPPLIER）"),
    Column("supplier_msg_handler", Text, comment="訊息處理方（KKDAY / SUPPLIER）"),
    # ── product_info 群組（ors_prod_setting 投影）──
    Column(
        "product_summary",
        JSONB,
        comment="商品摘要：category / timezone / product_name(單語) / sale_time_result",
    ),
    Column("product_desc_module", JSONB, comment="商品描述模組（單語渲染：行程／注意事項／介紹…）"),
    # ── item_info 群組 ──
    Column("item_lang", JSONB, comment="規格渲染文案（來源 ors_prod_lang.item_summary）"),
    Column(
        "item_setting",
        JSONB,
        comment="規格設定：spec_rule / price / quantity（來源 ors_prod_setting.item_summary）",
    ),
    # ── package_info 群組 ──
    Column("package_lang", JSONB, comment="方案渲染文案（來源 ors_prod_lang.package_summary）"),
    Column(
        "package_setting",
        JSONB,
        comment="本方案設定：多語名稱 / GPM / 退改（來源 ors_prod_setting.package_summary）",
    ),
    Column(
        "package_policy",
        JSONB,
        comment="方案政策：cancel_policy_client + tour_duration（來源 ors_pkg_basic）",
    ),
    Column(
        "package_module_setting",
        JSONB,
        comment=(
            "方案模組設定 list[{prod_module_type, prod_module_setting}]"
            "（來源 ors_prod_module_setting）"
        ),
    ),
    # ── meta 群組（快取管理，非業務資料）──
    Column(
        "create_date",
        DateTime(timezone=True),
        key="fetched_at",
        comment="本列自 production 取數的時間",
    ),
    Column("expires_at", DateTime(timezone=True), comment="快取到期時間；讀到過期視為 miss 並刪列"),
    Index("idx_evidence_snapshot_tbl_expires_at", "expires_at"),
    Index("idx_evidence_snapshot_tbl_prod_oid", "prod_oid"),
    Index("idx_evidence_snapshot_tbl_supplier_oid", "supplier_oid"),
    Index("idx_evidence_snapshot_tbl_unique01", "order_oid", unique=True),
    Column(
        "create_user", String(255), comment="建立者（SSO 接入前一律 system，接入後為使用者 email）"
    ),
    Column(
        "modify_user",
        String(255),
        comment="最後修改者（SSO 接入前一律 system，接入後為使用者 email；NULL＝從未修改）",
    ),
    Column("modify_date", DateTime(timezone=True), comment="最後修改時間"),
    comment="訂單佐證快照（qc_evidence 的 PG 快取層）：下單當時的商品／規格／方案內容投影，一訂單一列 "
    "+ TTL 懶清理。runtime 派生快取（真相源＝production snapshot，可重生），刻意不入資料包。"
    "欄位徹底拆開而非單一 payload jsonb——ID／純量各自成欄便於 grid 核對，內容各自獨立 jsonb 欄",
)

# ── engine（lazy；可由測試 set_engine 換成測試庫）───────────────────────────
_engine: Engine | None = None


def resolve_url() -> str:
    """生效的 SQLAlchemy URL（PostgreSQL；取自 config.env.database_url）。"""
    return env.database_url


def _engine_kwargs() -> dict:
    """create_engine 共用參數：連線池調校（get_engine / set_engine 同一組，避免兩處漂移）。

    pool_pre_ping：借用前 ping，避開 PG idle 斷線 / 重啟後借到死連線；
    pool_size/max_overflow/pool_recycle 由 env 調（見 config.py）——prejudge 64 執行緒共享，
    預設 15 不足故拉高，上限仍須 < PG max_connections。
    """
    return {
        "future": True,
        "pool_pre_ping": True,
        "pool_size": env.db_pool_size,
        "max_overflow": env.db_max_overflow,
        "pool_recycle": env.db_pool_recycle,
    }


def get_engine() -> Engine:
    """取當前 engine（首次依 resolve_url 建立）。db 子模組一律經此取連線。"""
    global _engine
    if _engine is None:
        _engine = create_engine(resolve_url(), **_engine_kwargs())
    return _engine


def set_engine(url: str) -> Engine:
    """重設 engine（測試指向測試庫 / 切換連線用）。"""
    global _engine
    _engine = create_engine(url, **_engine_kwargs())
    return _engine


def upsert(table: Table, values: dict, pk: list[str]):
    """INSERT … ON CONFLICT(pk) DO UPDATE（PostgreSQL；取代舊 sqlite INSERT OR REPLACE）。

    Args:
        table: 目標 Table。
        values: 欲寫入的欄位值 map。
        pk: 衝突鍵的**欄位 key**（Python 端名稱，單一或 composite）。

    Returns:
        可執行的 upsert statement。
    """
    # 只更新 values 內提供的欄位（minus pk）；未提供者保留既有，不被 NULL 覆蓋。
    stmt = _pg_insert(table).values(**values)
    update = {k: stmt.excluded[k] for k in values if k not in pk}
    # ⚠️ index_elements 傳 **Column 物件**而非字串：部分欄位的 DB 名與 Python key 不同
    # （DDL 規範改名後以 `Column(db名, key=原名)` 保留 Python/wire 名），傳字串會被當成
    # DB 欄名直接寫進 ON CONFLICT，撞 UndefinedColumn。
    return stmt.on_conflict_do_update(index_elements=[table.c[k] for k in pk], set_=update)

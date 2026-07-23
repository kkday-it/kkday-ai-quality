"""資料層 schema 與 engine（SQLAlchemy Core · PostgreSQL only）。

app 操作庫一律 PostgreSQL（對齊 QC DB）；連線取自 `config.env.database_url`
（dev 預設本機 `postgresql+psycopg2://localhost:5432/kkdb_ai_quality`，prod 經 env 覆蓋）。
db 子模組的函式皆走本模組的 engine + Table metadata；schema 演進由 Alembic 管（見 alembic/）。

時間欄位沿用 ISO 字串（Text，與既有 API 回傳形態一致）。
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    MetaData,
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
    "attributions",
    metadata,
    Column("finding_id", Text, primary_key=True),
    # ── 來源複合鍵 (source, source_id)：關聯回來源表（source 定表、source_id 對該表特徵 id）──
    # 取代舊 item_id 複合字串（`{source}-{natural_id}`）。source_id 存特徵 id 原值
    # （product_reviews→rec_oid / conversations→session_oid / freshdesk_tickets→id /
    #  app_feedback→oid / mixpanel_tracker→insert_id）。
    Column("source", Text),
    Column("source_id", Text),
    # ── 關聯 / 查詢便利欄（prod_oid 供 ProductDetail 下鑽）──
    Column("prod_oid", Text),
    # ── 傾向 / 階段 ──
    Column("polarity", Text),  # positive | negative | neutral
    # 情緒分 1-5（LLM 讀原文判；與 polarity 同段輸出：負面1-2/中立3/正面4-5）——與外部評論 sentiment
    # 同尺度，供評論對比表逐則比對；null＝未初判。
    Column("sentiment_score", Integer),
    Column("prejudge_stage", Text),  # judged / pending_review / pending_data（初判完成度）
    # ── 歸因分類 L1→L2（code + 中文 label；label 與 code 同存＝SSOT 即資料本身）──
    Column("l1_code", Text),
    Column("l1_label", Text),
    Column("l2_code", Text),
    Column("l2_label", Text),
    # ── 信心 ──
    Column("conf_value", Float),  # 最終信心（校準後）
    Column("conf_raw", Float),  # arbiter LLM 原始信心
    Column("conf_tier", Text),  # auto_accept / jury / needs_review
    # ── 初判內容 ──
    Column(
        "summary", JSONB
    ),  # 反饋摘要（語系→簡明摘要 map；務必含 zh-tw·表格只顯示 zh-tw；逐字原文佐證另存 evidence）
    Column("evidence", Text),  # 佐證原文（evidence_quote）
    Column("action", Text),  # 建議行動（recommended_action）
    # ── 元數據 ──
    Column("model", Text),  # 初判模型（stub 時為 "stub"）
    Column("is_primary", Boolean, server_default="false"),  # 多歸因主歸因旗標
    # ── 人工判決軸 ──
    Column(
        "verdict_status", Text
    ),  # 判決狀態：new / auto_confirmed(系統判決) / confirmed / dismissed
    # 人工判決 audit：誰、何時最後改了 status（人工確認/忽略/撤銷）——非系統自動路由；
    # 完整轉移軌跡在 attribution_history（kind='verdict'）。
    Column("verdict_by", Text),  # 判決人：人工＝email；系統判決＝system:auto_confirm
    Column("verdict_at", Text),  # 初判時間（ISO 字串，與 created_at 同形態）
    Column("needs_review", Boolean, server_default="false"),  # 人審佇列
    Column("created_at", Text),
    Index("idx_attributions_source", "source"),
    # (source, source_id) 複合索引：所有歸因查詢的 join / EXISTS 走此複合條件
    Index("idx_attributions_source_id", "source", "source_id"),
    # 列表深化篩選熱路徑（typed 欄直接 btree 索引，取代舊 JSONB expression 索引）
    Index("idx_attributions_polarity", "polarity"),
    Index("idx_attributions_prejudge_stage", "prejudge_stage"),
    Index("idx_attributions_l1", "l1_code"),
    # L2 taxonomy 子樹篩選 + 情緒分篩選熱路徑（原僅 l1 有索引，l2/sentiment 全表掃）
    Index("idx_attributions_l2", "l2_code"),
    Index("idx_attributions_sentiment", "sentiment_score"),
    Index("idx_attributions_tier", "conf_tier"),
)

# ── 5 反饋來源獨立實體表（各自對齊源表 schema，PK=特徵 id；欄位存原始源值 raw text）─────
# 統一經 source_registry（table + natural_key）+ config/ai_judge/source_mapping.json（源欄→canonical）
# 產出顯示層 canonical 欄（content/score/occurred_at…）。欄位一律 Text（忠實 raw；巢狀 JSON 於
# _enrich 端解析，如 product_reviews.order_snap_json → prod_name）。
product_reviews = Table(
    "product_reviews",
    metadata,
    Column("rec_oid", Text, primary_key=True),  # 特徵 id
    Column("member_uuid", Text),
    Column("create_date", Text),  # canonical occurred_at
    Column("rec_title", Text),  # canonical title
    Column("rec_desc", Text),  # canonical content（初判主輸入）
    Column("rec_scores", Text),  # canonical score
    Column("traveller_type", Text),
    Column("lang_code", Text),  # canonical lang
    Column("prod_oid", Text),
    Column("pkg_oid", Text),
    Column("order_oid", Text),
    Column("order_mid", Text),  # ⚠️ 會員 id（個資）
    Column("supplier_oid", Text),
    Column("order_snap_json", Text),  # 多語商品名快照 JSON（enrich 解析 prod_name/package_name）
    Column("lst_dt_go", Text),  # canonical go_date（出發日）
    Column("product_category", Text),  # 商品分類（enrich 解析 main/sub）
    Column(
        "review_external_lst_oid", Text
    ),  # 外部評論號（評論系統 rec_oid 對橋回查鍵；無對應為 NULL）
    Column("sentiment", Text),  # 外部 LLM 情緒分 1-5（輔助訊號·傾向以原文判定為準）
    Column(
        "free_tag", Text
    ),  # 外部 LLM 面向標籤 JSON 字串 [{tag_name,tag_value,tag_list}]（輔助訊號）
    Index("idx_product_reviews_create_date", "create_date"),
    Index("idx_product_reviews_prod_oid", "prod_oid"),
    Index("idx_product_reviews_product_category", "product_category"),
)

conversations = Table(
    "conversations",
    metadata,
    Column("session_oid", Text, primary_key=True),  # 特徵 id
    Column("zendesk_ticket_id", Text),
    Column("session_date_tw", Text),
    Column("session_datetime_tw", Text),  # canonical occurred_at
    Column("order_mid", Text),
    Column("order_oid", Text),
    Column("order_lang", Text),
    Column("order_price_pay", Text),
    Column("order_profit", Text),
    Column("order_create_source_code", Text),
    Column("prod_oid", Text),
    Column("product_name", Text),
    Column("prod_name_zh_tw", Text),
    Column("prod_bd_tag_note", Text),
    Column("product_category", Text),
    Column("order_go_date", Text),
    Column("product_timezone", Text),
    Column("trip_stage", Text),
    Column("order_status", Text),
    Column("supplier_oid", Text),
    Column("supplier_name", Text),
    Column("msg_handler", Text),
    Column("review_score", Text),
    Column("review_content", Text),
    Column("cs_task_type_name", Text),
    Column("inbound_session_count", Text),
    Column("conversation_type", Text),  # canonical channel
    Column("user_msg_count", Text),
    Column("agent_msg_count", Text),
    Column("chatbot_conversation", Text),  # canonical content（併 human_conversation）
    Column("human_conversation", Text),  # canonical content（併 chatbot_conversation）
    Column("session_direction", Text),
    Index("idx_conversations_datetime", "session_datetime_tw"),
    Index("idx_conversations_prod_oid", "prod_oid"),
)

freshdesk_tickets = Table(
    "freshdesk_tickets",
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
    Index("idx_freshdesk_created_at", "created_at"),
    Index("idx_freshdesk_product_id", "product_id"),
)

app_feedback = Table(
    "app_feedback",
    metadata,
    Column("oid", Text, primary_key=True),  # 特徵 id
    Column("created_datetime", Text),  # canonical occurred_at
    Column("comment", Text),  # canonical content
    Column("score", Text),  # canonical score
    Column("source", Text),  # 來源渠道（app 端，與 attributions.source 不同語意）
    Column("lang_code", Text),  # canonical lang
    Column("version", Text),
    Index("idx_app_feedback_created", "created_datetime"),
)

mixpanel_tracker = Table(
    "mixpanel_tracker",
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
    Index("idx_mixpanel_time", "time"),
)

batches = Table(
    "batches",
    metadata,
    Column("batch_id", Text, primary_key=True),
    Column("name", Text),
    Column("source", Text),
    Column("original_name", Text),
    Column("row_count", Integer),
    Column("inserted_count", Integer),
    Column("uploaded_at", Text),
    Column("note", Text),  # 用戶上傳時輸入的備註（每工作表一則，隨批次保存）
)

# 全項目共享設定（單例 row：key 固定 "__global__"，見 core/settings.py）；data＝JSON 全文（機密欄位 at-rest 加密）。
settings = Table(
    "settings",
    metadata,
    Column("key", Text, primary_key=True),
    Column("data", Text),
    Column("updated_at", Text),
)

# ── 初判規則版本（product_vertical/source_mapping + prompt_* 的 live + 歷史）───
# append-only 快照：每次存檔 insert 新版本列（不就地改），規避 JSONB write-amplification。
# 檔案 config/ai_judge/*.json（product_vertical/source_mapping）與
# prompts/*.md（prompt_*）為默認 seed；DB 存 live + 完整歷史；一 rule_code 僅一 active。
# 版本化 rule_code：product_vertical / source_mapping / prompt_polarity / prompt_C-1~6。
judge_rule_versions = Table(
    "judge_rule_versions",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column(
        "rule_code", Text, nullable=False
    ),  # 'product_vertical' | 'source_mapping' | 'prompt_polarity' | 'prompt_C-1'..'prompt_C-6'
    Column("version", Integer, nullable=False),  # per rule_code 遞增
    Column("content", JSONB, nullable=False),  # 完整 rule/schema JSON
    Column("note", Text),
    Column("author", Text),  # user_id
    Column("is_active", Boolean, nullable=False, server_default="false"),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
    UniqueConstraint("rule_code", "version", name="uq_judge_rule_code_version"),
    # 一 rule_code 僅一 active（部分唯一索引）
    Index(
        "uq_judge_rule_active",
        "rule_code",
        unique=True,
        postgresql_where=text("is_active"),
    ),
)

# 初判 Prompt 草稿（prompt_polarity / prompt_C-1~6 每 rule_code 一份共享草稿）。與 judge_rule_versions
# 分離：版本表維持「存檔即 active」單一語意；草稿＝未入庫的編輯中內容——沙盒可直接送測（雙跑對比），
# 驗證滿意後一鍵入庫成新 active 版（隨後刪本列），或捨棄。併發=last-write-wins，
# updated_by/updated_at 供前端顯示編輯線索；base_version 供 stale 偵測（< active 版本號時提示分叉過時）。
prompt_drafts = Table(
    "prompt_drafts",
    metadata,
    Column("rule_code", Text, primary_key=True),  # 'prompt_polarity' | 'prompt_C-1'..'prompt_C-6'
    Column(
        "content", JSONB, nullable=False
    ),  # {"_meta":..., "text": md 全文}（同 rule content 格式）
    Column("base_version", Integer, nullable=False),  # 從哪個版本分叉
    Column("updated_by", Text),  # 最後編輯人（user email）
    Column("updated_at", DateTime(timezone=True), server_default=func.now()),
)

# 歸因備註（append-only 歷史；每條歸因 finding_id 可累積多則備註，記備註人/時間/內容）。
# 獨立表：重新初判（replace_source_findings 刪+插 attributions）不影響備註（依 finding_id 關聯，同域重新初判 id 不變）。
finding_notes = Table(
    "finding_notes",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("finding_id", Text, nullable=False),  # 對應 attributions.finding_id（該條歸因分類）
    Column("author", Text, nullable=False),  # 備註人（user email）
    Column("content", Text, nullable=False),  # 備註內容
    Column("created_at", DateTime(timezone=True), server_default=func.now()),  # 備註時間
    Index("idx_finding_notes_finding", "finding_id"),
)

# AI 使用紀錄（per-call：每次真 LLM 呼叫落一列，供消耗 dashboard 多維度聚合）。
# 唯一寫入點＝llm.client.chat_json 的 usage recorder（批次 buffer 批量寫 / 單次即時寫）。
llm_usage = Table(
    "llm_usage",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("stage", Text),  # 呼叫階段：polarity/attribute/domain/attribute_b/translate
    Column("model", Text, nullable=False),  # 使用模型（cfg.model）
    Column("provider", Text),  # 供應商 id（settings.provider_id_for(base_url) 反推）
    Column("prompt_tokens", Integer),  # 輸入 token
    Column("completion_tokens", Integer),  # 輸出 token（reasoning model 下含 reasoning_tokens）
    Column(
        "reasoning_tokens", Integer
    ),  # completion 中的 reasoning 部分（gpt-5 reasoning_effort 產；量測降 effort 空間）
    Column("cached_tokens", Integer),  # prompt 中命中 prompt cache 的部分（折扣計價）
    Column("total_tokens", Integer),  # prompt + completion
    Column("cost_usd", Float),  # pricing.cost_usd 換算（含 cache 折扣）
    Column("source", Text),  # 初判來源（product_reviews…；ad-hoc 呼叫可空）
    Column("source_id", Text),  # 該來源特徵 id（可空）
    Column("job_id", Text),  # 批次任務 id（單次呼叫為空）
    Column("created_at", DateTime(timezone=True), server_default=func.now()),  # 呼叫時間
    Index("idx_llm_usage_created_at", "created_at"),
    Index("idx_llm_usage_model", "model"),
    Index("idx_llm_usage_stage", "stage"),
)

# 歸因歷史（run 級：每次觸發 LLM 歸因的動作——批量初判 / 選取多筆 / 單筆重新初判——落一列）。
# 與 llm_usage（call 級）以 job_id 關聯：run 存業務語境（誰/何時/範圍/參數/結果統計），
# per-stage token/費用明細由 llm_usage 聚合（job 結束 flush 後可查）。寫入點＝prejudge_batch。
prejudge_runs = Table(
    "prejudge_runs",
    metadata,
    Column("job_id", Text, primary_key=True),  # 批次任務 id（pj_*；與 llm_usage.job_id 對齊）
    Column(
        "kind", Text, nullable=False
    ),  # 觸發型態：batch（scope=all 目標選取）/ selected（顯式多筆）/ single（單筆）
    Column(
        "rejudge", Boolean
    ),  # 標的先前已有初判 → 重新初判（single/selected 查 attributions；batch 依 stages 含已初判階段）
    Column("source", Text),  # 反饋來源 code（product_reviews…）
    Column("model", Text),  # 主初判模型
    Column("params", JSONB),  # 發起參數快照（stages/verticals/傾向/信心上限…；item_ids 只留樣本）
    Column("status", Text, nullable=False),  # running/paused/cancelling → 終態 done/error/cancelled
    Column("total", Integer),  # 標的筆數
    Column("processed", Integer),  # 已處理（終態回寫；執行中由 in-mem 快照 overlay）
    Column("ok", Integer),  # 成功筆數
    Column("failed", Integer),  # 失敗筆數
    Column("total_tokens", BigInteger),  # 本 run 累計 token（usage sink 加總）
    Column("cost_usd", Float),  # 本 run 累計費用（pricing 換算）
    Column("triggered_by", Text),  # 觸發人（user email）
    Column("started_at", DateTime(timezone=True), server_default=func.now()),
    Column("finished_at", DateTime(timezone=True)),  # 終態時間（執行中為空）
    # run_log.read(job_id) 快照（entries 陣列）：僅小批量 job 有收集（見 run_log.LOG_JOB_MAX_ITEMS），
    # job 結束落庫供歸因歷史「查看 LLM 日誌」事後回看；仿 prompt_sandbox_runs.log 同一模式。
    Column("log", JSONB),
    Index("idx_prejudge_runs_started_at", "started_at"),
)

# 歸因歷史（評論級 append-only：每則評論 (source, source_id) 的歷次初判快照 / 判決轉移 / 備註）。
# 補 attributions「刪+插」重新初判不留痕的缺口（prejudge_runs 是 run 級、llm_usage 是 call 級，皆無法
# 重建單一評論的初判演進）。寫入點：judgment（replace_source_findings 同交易去重寫入，model+params
# +result_digest 全同前一筆即 skip）/ status（update_finding_status 轉移時）/ note（使用者手動）。
# 無 FK：finding_id 重新初判會更換，比照 finding_notes/prejudge_runs 慣例以邏輯鍵關聯。
attribution_history = Table(
    "attribution_history",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("source", Text, nullable=False),  # 反饋來源 code（product_reviews…）
    Column("source_id", Text, nullable=False),  # 該來源特徵 id（評論級鍵）
    Column(
        "kind", Text, nullable=False
    ),  # 事件類型：judgment（初判快照）/ status（判決轉移）/ note
    Column("model", Text),  # 初判模型（kind=judgment；stub 同 attributions.model 語意）
    # 事件細節：judgment 存 {model, …}（精餾小字典，回填列為 {"backfilled": true}）；
    # status 存 {finding_id, from, to}（評論級表不加 finding_id 欄，避免對 judgment/note 恆 NULL 的稀疏欄）。
    Column("params", JSONB),
    Column("attributions", JSONB),  # 初判快照（attribution_dto 形狀陣列；kind=judgment 才有）
    Column("result_digest", Text),  # 快照全欄位（排時間戳）正規化 sha256，供去重比對
    Column("job_id", Text),  # 觸發批次（與 prejudge_runs.job_id 對齊；status/note 為空）
    Column("triggered_by", Text),  # 觸發人（user email；kind=judgment）
    Column("author", Text),  # 操作者/備註人（kind=status/note）
    Column("content", Text),  # 備註內容（kind=note）
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
    # 評論時間軸查詢熱路徑：(source, source_id) 定位 + created_at 排序
    Index("idx_attribution_history_source_id", "source", "source_id", "created_at"),
    Index("idx_attribution_history_created_at", "created_at"),
    # latest_snapshots（DISTINCT ON）快照查詢熱路徑 partial index——登記於 metadata 使
    # create_all（空庫路徑）與 migration（既有庫路徑）產出一致，消弭兩路徑 schema drift
    Index(
        "idx_attribution_history_snapshot",
        "source",
        "model",
        "source_id",
        text("created_at DESC"),
        postgresql_where=text("kind = 'prejudge'"),
    ),
)

# 歸因列表 Prompt 測試沙盒歷史（與 attributions/attribution_history/prejudge_runs 完全分離——沙盒測試
# 不落正式初判，只落此表）。一 run＝對 item_ids 逐筆跑 prompt_ids 子集的一次沙盒測試，results 含
# 逐筆逐 prompt 結果，log 為該次 run_log 快照（供事後回看完整 LLM log；run_log 本身純記憶體不落庫）。
prompt_sandbox_runs = Table(
    "prompt_sandbox_runs",
    metadata,
    Column("run_id", Text, primary_key=True),  # psbx_* uuid4 hex
    Column("source", Text, nullable=False),  # 來源 code（product_reviews…）
    Column(
        "scope", Text, nullable=False
    ),  # single（單列）/ selection（勾選多筆）/ all（依條件批量選取）
    Column("item_ids", JSONB, nullable=False),  # 受測 source_id 清單
    Column("prompt_ids", JSONB, nullable=False),  # 勾選的 prompt（polarity / C-1..C-6）
    Column("item_count", Integer, nullable=False),
    Column("results", JSONB, nullable=False),  # 逐筆 × 各 prompt 結果（sandbox_classify 輸出集合）
    Column("log", JSONB, nullable=False),  # run_log.read(job_id) 快照（entries 陣列）
    Column("model", Text),  # 初判模型
    Column("triggered_by", Text),  # 觸發人（user email）
    Column("job_id", Text),  # 對應 run_log job_id（供除錯追溯；log 已快照，非用於即時查詢）
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
    # 版本選擇功能（見 app.judge.prompt_source.load 的 versions 參數）：本次測試 7 條 prompt 各自
    # 用哪個版本，非 active 才需記（active 沿用 judge_rule_versions 當下狀態，事後可回推）。
    Column("versions", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    # 草稿測試（見 app.judge.prompt_source.load 的 drafts 參數）：本次測試各 prompt 的草稿 md 全文
    # 快照（{rule_code: md 全文}）——run 與草稿後續演進脫鉤，事後可溯源當時測的是什麼內容。
    Column("drafts", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    # 雙跑對比模式：true 時 results 逐筆為 {source_id, text, compare, baseline:{...}, draft:{...}}
    # （基準版 vs 草稿版各跑一次）；false 維持單跑形狀（歷史 run 相容）。
    Column("compare", Boolean, nullable=False, server_default=text("false")),
    # 歷史列表熱路徑：依時間倒序
    Index("idx_prompt_sandbox_runs_created", "created_at"),
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
    "evidence_snapshot",
    metadata,
    Column("order_oid", BigInteger, primary_key=True),
    # order_summary 群組（order_tbl 五欄；無歧義不需前綴）
    Column("order_mid", Text),
    Column("order_status", Text),
    Column("price_pay", Float),
    Column("lang_code", Text),
    Column("crt_dt", DateTime(timezone=True)),
    # order_summary 群組（order_lst 八欄；pkg_oid=prod_level2_oid、pkg_name=prod_level2_name）
    Column("prod_oid", BigInteger),
    Column("prod_version", BigInteger),
    Column("pkg_oid", BigInteger),
    Column("item_oid", BigInteger),
    Column("supplier_oid", BigInteger),
    Column("lst_dt_go", DateTime(timezone=True)),
    Column("timezone", Text),
    Column("pkg_name", Text),
    Column("prod_desc", Text),
    # supplier_info 群組
    Column("supplier_name", Text),
    Column("supplier_order_handler", Text),
    Column("supplier_msg_handler", Text),
    # product_info 群組（ors_prod_setting 投影）
    Column("product_summary", JSONB),  # category/timezone/product_name(單語)/sale_time_result
    Column("product_desc_module", JSONB),  # description_module（單語渲染：行程/注意事項/介紹…）
    # item_info 群組
    Column("item_lang", JSONB),  # ors_prod_lang.item_summary（規格渲染文案）
    Column(
        "item_setting", JSONB
    ),  # ors_prod_setting.item_summary（規格設定：spec_rule/price/quantity）
    # package_info 群組
    Column("package_lang", JSONB),  # ors_prod_lang.package_summary（方案渲染文案）
    Column("package_setting", JSONB),  # ors_prod_setting.package_summary[本方案]（多語名/GPM/退改）
    Column("package_policy", JSONB),  # ors_pkg_basic（cancel_policy_client + tour_duration）
    Column(
        "package_module_setting", JSONB
    ),  # ors_prod_module_setting（list[{prod_module_type,...}]）
    # meta 群組（快取管理，非業務資料）
    Column("fetched_at", DateTime(timezone=True)),
    Column("expires_at", DateTime(timezone=True)),
    Index("idx_evidence_snapshot_expires", "expires_at"),
    Index("idx_evidence_snapshot_prod_oid", "prod_oid"),
    Index("idx_evidence_snapshot_supplier_oid", "supplier_oid"),
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
        pk: 衝突鍵欄位名（單一或 composite）。

    Returns:
        可執行的 upsert statement。
    """
    # 只更新 values 內提供的欄位（minus pk）；未提供者保留既有，不被 NULL 覆蓋。
    stmt = _pg_insert(table).values(**values)
    update = {k: stmt.excluded[k] for k in values if k not in pk}
    return stmt.on_conflict_do_update(index_elements=pk, set_=update)

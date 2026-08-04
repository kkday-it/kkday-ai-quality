"""為 9 張自建表補 COMMENT ON（表 9 + 欄 118）

KKday DDL 規範要求每張表與每個欄位都有 COMMENT。本支把 `tables.py` 的 `Table(comment=)` /
`Column(comment=)` 內容落到 PG catalog——兩者為同一份文字，`tables.py` 是唯一真相源。

⚠️ 本檔的 SQL 是**凍結快照**（產生當下自 metadata 導出後寫死），不在 migration 執行期讀
`tables.py`。理由同 baseline v2：migration 一旦讀當下的 code，語意就會隨 code 漂移，
「這支 revision 做了什麼」不再可重現——2026-08-04 的 baseline v1 正是這樣把鏈弄斷的。
日後改註解＝改 `tables.py` + 開新 revision，不回頭改本檔。

範圍：**只含 9 張自建表**。5 張來源鏡像表（reviews / conversations / freshdesk_tickets /
app_feedback / mixpanel_tracker）忠實鏡射上游 BQ 取數輸出，已向 DBA 列入豁免申請，本支不動。

無風險：COMMENT ON 只寫 pg_description（catalog-only），不鎖表、不阻塞讀寫、完全可逆。

Revision ID: f3d92a7c48be
Revises: e5b83c214f7d
Create Date: 2026-08-04

"""

from collections.abc import Sequence

from alembic import op

revision: str = "f3d92a7c48be"
down_revision: str | Sequence[str] | None = "e5b83c214f7d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 表 9 + 欄 118（產生當下自 metadata 導出的凍結快照，見檔頭說明）
_COMMENTS: tuple[str, ...] = (
    # ── attributions ──
    "COMMENT ON TABLE attributions IS '初判歸因結果（一列＝一條歸因，同一則反饋可有多列）。全 typed scalar 欄無 JSONB blob——本表是查詢／聚合／篩選密集的分析核心，typed 欄可直接 btree 索引且 SQL 乾淨；巢狀物件屬呈現層，在 API DTO（_shared.attribution_dto）才組。無任何人工可改欄位，重新初判即整組替換'",
    "COMMENT ON COLUMN attributions.finding_id IS '歸因 id（每次重新初判整組替換，非穩定鍵）'",
    "COMMENT ON COLUMN attributions.source IS '反饋來源 code，決定關聯哪張來源表（reviews / conversations…）'",
    "COMMENT ON COLUMN attributions.source_id IS '該來源表的特徵 id 原值（reviews→rec_oid / conversations→session_oid / freshdesk_tickets→id / app_feedback→oid / mixpanel_tracker→insert_id）'",
    "COMMENT ON COLUMN attributions.polarity IS '情緒傾向：positive / negative / neutral'",
    "COMMENT ON COLUMN attributions.sentiment_score IS '情緒分 1-5（LLM 讀原文判定，與 polarity 同段輸出：負 1-2 / 中 3 / 正 4-5）——與外部評論 sentiment 同尺度供逐則比對；null＝未初判'",
    "COMMENT ON COLUMN attributions.prejudge_stage IS '初判完成度：judged（已初判）/ pending_review（待複審）/ pending_data（待數據補充）'",
    "COMMENT ON COLUMN attributions.l1_code IS 'L1 域代碼（如 content / supplier）'",
    "COMMENT ON COLUMN attributions.l1_label IS 'L1 域中文名'",
    "COMMENT ON COLUMN attributions.l2_code IS 'L2 面向代碼（C-N-M）'",
    "COMMENT ON COLUMN attributions.l2_label IS 'L2 面向中文名'",
    "COMMENT ON COLUMN attributions.conf_value IS '最終信心值（校準後，0~1）'",
    "COMMENT ON COLUMN attributions.conf_raw IS 'arbiter LLM 回報的原始信心值（未校準）'",
    "COMMENT ON COLUMN attributions.conf_tier IS '信心分層：auto_accept（自動採信）/ jury（評審複審）/ needs_review（人工複審）'",
    "COMMENT ON COLUMN attributions.summary IS '反饋摘要（語系→簡明摘要 map，務必含 zh-tw；表格只顯示 zh-tw，逐字原文佐證另存 evidence）'",
    "COMMENT ON COLUMN attributions.evidence IS '佐證原文（自反饋原文逐字擷取的片段）'",
    "COMMENT ON COLUMN attributions.action IS '建議行動'",
    "COMMENT ON COLUMN attributions.model IS '產出本列的初判模型；stub 模式為 ''stub'''",
    "COMMENT ON COLUMN attributions.is_primary IS '多歸因中的主歸因旗標'",
    "COMMENT ON COLUMN attributions.is_auto_accepted IS '系統是否自動採納：信心達 auto_accept 門檻且非 needs_review 階段時由 prejudge._route_auto_accept 設 true；jury 分層與低信心重路由留 false'",
    "COMMENT ON COLUMN attributions.created_at IS '初判落庫時間（ISO 8601 字串）＝本列唯一時間源'",
    # ── attribution_history ──
    "COMMENT ON TABLE attribution_history IS '評論級歸因歷史（append-only 事件流）：補 attributions「刪+插」重新初判不留痕的缺口——prejudge_runs 是 run 級、llm_usage 是 call 級，皆無法重建單一評論的初判演進。無 FK，以 (source, source_id) 邏輯鍵關聯（該鍵跨重新初判穩定，不像 finding_id 會斷鏈）'",
    "COMMENT ON COLUMN attribution_history.id IS '事件流水號'",
    "COMMENT ON COLUMN attribution_history.source IS '反饋來源 code（reviews…）'",
    "COMMENT ON COLUMN attribution_history.source_id IS '該來源的特徵 id（評論級鍵）'",
    "COMMENT ON COLUMN attribution_history.kind IS '事件類型：prejudge（初判快照）/ note（人工備註）/ failure（初判失敗留痕）/ router_shadow（域路由召回量測，內部遙測不進使用者時間軸）'",
    "COMMENT ON COLUMN attribution_history.model IS '初判模型（kind=prejudge）'",
    "COMMENT ON COLUMN attribution_history.params IS '事件細節：prejudge 存 {model,…}（回填列為 {backfilled:true}）；failure 存 {error}；router_shadow 存 {candidates,hit,missed,probs}'",
    "COMMENT ON COLUMN attribution_history.attributions IS '初判結果快照（attribution_dto 形狀陣列；僅 kind=prejudge 有值）'",
    "COMMENT ON COLUMN attribution_history.result_digest IS '快照全欄位（排除時間戳）正規化後的 sha256，供相鄰列去重比對'",
    "COMMENT ON COLUMN attribution_history.job_id IS '觸發的批次任務 id（與 prejudge_runs.job_id 對齊）；人工事件為空'",
    "COMMENT ON COLUMN attribution_history.triggered_by IS '觸發人（user email；kind=prejudge）'",
    "COMMENT ON COLUMN attribution_history.author IS '備註人（user email；kind=note）'",
    "COMMENT ON COLUMN attribution_history.content IS '備註內容（kind=note）'",
    "COMMENT ON COLUMN attribution_history.created_at IS '事件時間'",
    # ── llm_usage ──
    "COMMENT ON TABLE llm_usage IS 'AI 使用紀錄（per-call：每次真實 LLM 呼叫落一列），供成本 dashboard 多維度聚合。唯一寫入點＝llm.client 的 usage recorder（批次走 buffer 批量寫、單次即時寫）'",
    "COMMENT ON COLUMN llm_usage.id IS '用量列流水號'",
    "COMMENT ON COLUMN llm_usage.stage IS '呼叫階段：polarity / C-1~C-6 / prompt_debug / prompt_revise…'",
    "COMMENT ON COLUMN llm_usage.model IS '實際使用的模型（cfg.model）'",
    "COMMENT ON COLUMN llm_usage.prompt_tokens IS '輸入 token 數'",
    "COMMENT ON COLUMN llm_usage.completion_tokens IS '輸出 token 數（reasoning model 下已含 reasoning_tokens）'",
    "COMMENT ON COLUMN llm_usage.reasoning_tokens IS 'completion 中屬 reasoning 的部分（reasoning_effort 產出；供量測降檔位的空間）'",
    "COMMENT ON COLUMN llm_usage.cached_tokens IS 'prompt 中命中 prompt cache 的 token 數（折扣計價）'",
    "COMMENT ON COLUMN llm_usage.total_tokens IS 'prompt_tokens + completion_tokens'",
    "COMMENT ON COLUMN llm_usage.cost_usd IS '本次呼叫費用（pricing.cost_usd 換算，含 cache 折扣與 service tier）'",
    "COMMENT ON COLUMN llm_usage.source IS '反饋來源 code（reviews…）；ad-hoc 呼叫為空'",
    "COMMENT ON COLUMN llm_usage.job_id IS '所屬批次任務 id；單次呼叫為空'",
    "COMMENT ON COLUMN llm_usage.created_at IS '呼叫時間'",
    # ── prejudge_runs ──
    "COMMENT ON TABLE prejudge_runs IS '初判批次執行紀錄（run 級：每次觸發初判的動作落一列）。與 llm_usage（call 級）以 job_id 關聯——本表存業務語境（誰／何時／範圍／參數／結果統計），token 與費用明細由 llm_usage 聚合'",
    "COMMENT ON COLUMN prejudge_runs.job_id IS '批次任務 id（pj_* uuid；與 llm_usage.job_id 對齊）'",
    "COMMENT ON COLUMN prejudge_runs.kind IS '觸發型態：batch（依條件批量選取）/ selected（勾選多筆）/ single（單筆）'",
    "COMMENT ON COLUMN prejudge_runs.rejudge IS '標的先前已有初判結果 → 本次為重新初判'",
    "COMMENT ON COLUMN prejudge_runs.source IS '反饋來源 code（reviews…）'",
    "COMMENT ON COLUMN prejudge_runs.model IS '本次使用的初判模型'",
    "COMMENT ON COLUMN prejudge_runs.params IS '發起參數快照（stages/verticals/傾向/信心上限…；item_ids 只留樣本避免膨脹）'",
    "COMMENT ON COLUMN prejudge_runs.status IS 'running / paused / cancelling → 終態 done / error / cancelled / interrupted（行程重啟）'",
    "COMMENT ON COLUMN prejudge_runs.total IS '本次標的總筆數'",
    "COMMENT ON COLUMN prejudge_runs.processed IS '已處理筆數（終態回寫；執行中由 in-mem 快照 overlay）'",
    "COMMENT ON COLUMN prejudge_runs.ok IS '成功筆數'",
    "COMMENT ON COLUMN prejudge_runs.failed IS '失敗筆數'",
    "COMMENT ON COLUMN prejudge_runs.total_tokens IS '本 run 累計 token（usage sink 加總）'",
    "COMMENT ON COLUMN prejudge_runs.cost_usd IS '本 run 累計費用（pricing 換算）'",
    "COMMENT ON COLUMN prejudge_runs.triggered_by IS '觸發人（user email）'",
    "COMMENT ON COLUMN prejudge_runs.started_at IS '開始時間'",
    "COMMENT ON COLUMN prejudge_runs.finished_at IS '結束時間；執行中為空'",
    "COMMENT ON COLUMN prejudge_runs.log IS 'run_log 快照（entries 陣列）：僅小批量 job 收集，供事後回看完整 LLM 日誌；run_log 本身純記憶體不落庫'",
    # ── prompt_debug_reviews ──
    "COMMENT ON TABLE prompt_debug_reviews IS '售後根因調試台的人工評判案例庫（一列＝一個被人工判過對錯的 session）。用途有二：① 餵給 AI 定點改寫當作「這裡判錯了、正解是這個」的證據 ② 改完 Prompt 後整批回歸重跑，驗證修好了舊案例、且沒順手改壞別的。刻意不存 Prompt 全文快照——版本檔 append-only，靠 prompt_version 回查即可'",
    "COMMENT ON COLUMN prompt_debug_reviews.id IS '案例流水號'",
    "COMMENT ON COLUMN prompt_debug_reviews.conversation IS '當時的調試文本原文（完整 IM session）'",
    "COMMENT ON COLUMN prompt_debug_reviews.ai_output IS 'AI 判定的全部欄位（原樣保留，未過濾）'",
    "COMMENT ON COLUMN prompt_debug_reviews.corrections IS '人標的正解 {欄名: 正解值}；只存被標錯的欄，全欄皆對則為 {}（正例，回歸時防過度矯正）'",
    "COMMENT ON COLUMN prompt_debug_reviews.confirmed IS '人明確標「對」的欄名清單。與 corrections 一起構成完整回歸判準——前者是「改完要變成這樣」、後者是「改完不准變」；兩者都沒出現的欄＝人沒看過，回歸不計分（拿 AI 原判當標準答案會讓分數憑空虛高）'",
    "COMMENT ON COLUMN prompt_debug_reviews.comment IS '人寫的整體修改建議（自由文字）'",
    "COMMENT ON COLUMN prompt_debug_reviews.prompt_version IS '當時使用的 Prompt 版本名；空字串＝送出前在頁面臨時編輯過、無對應存檔版本'",
    "COMMENT ON COLUMN prompt_debug_reviews.model IS '當時使用的模型'",
    "COMMENT ON COLUMN prompt_debug_reviews.reviewer IS '評判人（user email）'",
    "COMMENT ON COLUMN prompt_debug_reviews.created_at IS '建立時間'",
    # ── judge_rule_versions ──
    "COMMENT ON TABLE judge_rule_versions IS '初判規則版本庫（append-only 快照：每次存檔 insert 新列不就地改，規避 JSONB write-amplification）。檔案 config/*.json 與 prompts/*.md 為默認 seed，本表存 live + 完整歷史'",
    "COMMENT ON COLUMN judge_rule_versions.id IS '版本列流水號'",
    "COMMENT ON COLUMN judge_rule_versions.rule_code IS '規則代碼：bd_tag_vertical / source_mapping / prompt_polarity / prompt_C-1~C-6'",
    "COMMENT ON COLUMN judge_rule_versions.version IS '版本號，per rule_code 從 1 遞增'",
    "COMMENT ON COLUMN judge_rule_versions.content IS '該版本完整內容；prompt_* 為 {_meta, text(md 全文)}'",
    "COMMENT ON COLUMN judge_rule_versions.note IS '存檔備註（使用者輸入，說明本次改了什麼）'",
    "COMMENT ON COLUMN judge_rule_versions.author IS '存檔人（user email）'",
    "COMMENT ON COLUMN judge_rule_versions.is_active IS '是否為線上生效版；一 rule_code 僅一筆為 true（由部分唯一索引強制）'",
    "COMMENT ON COLUMN judge_rule_versions.created_at IS '存檔時間'",
    # ── batches ──
    "COMMENT ON TABLE batches IS '上傳批次審計流水：一列＝一次上傳中的一張工作表，供資料上傳頁回溯來源檔與筆數'",
    "COMMENT ON COLUMN batches.batch_id IS '上傳批次 id（uuid hex）'",
    "COMMENT ON COLUMN batches.name IS '自動命名的批次名「{來源} YYYYMMDD{當天序號:02d}」'",
    "COMMENT ON COLUMN batches.source IS '反饋來源 code（reviews / conversations…）'",
    "COMMENT ON COLUMN batches.original_name IS '上傳檔名（多分頁 xlsx 為「檔名::工作表名」）'",
    "COMMENT ON COLUMN batches.row_count IS '該工作表的資料列數'",
    "COMMENT ON COLUMN batches.uploaded_at IS '上傳時間（ISO 8601 字串，含時區偏移）'",
    "COMMENT ON COLUMN batches.note IS '使用者上傳時輸入的備註（每工作表一則，隨批次保存）'",
    # ── settings ──
    "COMMENT ON TABLE settings IS '全項目共享設定（單例 row，見 core/settings.py）：LLM 連線與模型配置庫、QC DB 連線、功能區綁定、導出偏好。機密欄位加密後才落此表'",
    "COMMENT ON COLUMN settings.key IS '設定鍵；目前僅單例 ''__global__'''",
    "COMMENT ON COLUMN settings.data IS '設定全文 JSON 字串；機密欄位（token/密碼）為 at-rest 密文'",
    "COMMENT ON COLUMN settings.updated_at IS '最後更新時間（ISO 8601 字串）'",
    # ── evidence_snapshot ──
    "COMMENT ON TABLE evidence_snapshot IS '訂單佐證快照（qc_evidence 的 PG 快取層）：下單當時的商品／規格／方案內容投影，一訂單一列 + TTL 懶清理。runtime 派生快取（真相源＝production snapshot，可重生），刻意不入資料包。欄位徹底拆開而非單一 payload jsonb——ID／純量各自成欄便於 grid 核對，內容各自獨立 jsonb 欄'",
    "COMMENT ON COLUMN evidence_snapshot.order_oid IS '訂單 oid（一訂單一列）'",
    "COMMENT ON COLUMN evidence_snapshot.order_mid IS '訂單編號（對外顯示用的訂單號）'",
    "COMMENT ON COLUMN evidence_snapshot.order_status IS '下單當時的訂單狀態'",
    "COMMENT ON COLUMN evidence_snapshot.price_pay IS '實付金額'",
    "COMMENT ON COLUMN evidence_snapshot.lang_code IS '訂單語系'",
    "COMMENT ON COLUMN evidence_snapshot.crt_dt IS '訂單建立時間'",
    "COMMENT ON COLUMN evidence_snapshot.prod_oid IS '商品 oid'",
    "COMMENT ON COLUMN evidence_snapshot.prod_version IS '下單當時的商品版本號（佐證取的是這一版內容）'",
    "COMMENT ON COLUMN evidence_snapshot.pkg_oid IS '方案 oid（來源欄 prod_level2_oid）'",
    "COMMENT ON COLUMN evidence_snapshot.item_oid IS '規格 oid'",
    "COMMENT ON COLUMN evidence_snapshot.supplier_oid IS '供應商 oid'",
    "COMMENT ON COLUMN evidence_snapshot.lst_dt_go IS '出發／使用日期'",
    "COMMENT ON COLUMN evidence_snapshot.timezone IS '商品所在時區'",
    "COMMENT ON COLUMN evidence_snapshot.pkg_name IS '方案名稱（來源欄 prod_level2_name）'",
    "COMMENT ON COLUMN evidence_snapshot.prod_desc IS '商品名稱／描述'",
    "COMMENT ON COLUMN evidence_snapshot.supplier_name IS '供應商名稱'",
    "COMMENT ON COLUMN evidence_snapshot.supplier_order_handler IS '訂單處理方（KKDAY / SUPPLIER）'",
    "COMMENT ON COLUMN evidence_snapshot.supplier_msg_handler IS '訊息處理方（KKDAY / SUPPLIER）'",
    "COMMENT ON COLUMN evidence_snapshot.product_summary IS '商品摘要：category / timezone / product_name(單語) / sale_time_result'",
    "COMMENT ON COLUMN evidence_snapshot.product_desc_module IS '商品描述模組（單語渲染：行程／注意事項／介紹…）'",
    "COMMENT ON COLUMN evidence_snapshot.item_lang IS '規格渲染文案（來源 ors_prod_lang.item_summary）'",
    "COMMENT ON COLUMN evidence_snapshot.item_setting IS '規格設定：spec_rule / price / quantity（來源 ors_prod_setting.item_summary）'",
    "COMMENT ON COLUMN evidence_snapshot.package_lang IS '方案渲染文案（來源 ors_prod_lang.package_summary）'",
    "COMMENT ON COLUMN evidence_snapshot.package_setting IS '本方案設定：多語名稱 / GPM / 退改（來源 ors_prod_setting.package_summary）'",
    "COMMENT ON COLUMN evidence_snapshot.package_policy IS '方案政策：cancel_policy_client + tour_duration（來源 ors_pkg_basic）'",
    "COMMENT ON COLUMN evidence_snapshot.package_module_setting IS '方案模組設定 list[{prod_module_type,…}]（來源 ors_prod_module_setting）'",
    "COMMENT ON COLUMN evidence_snapshot.fetched_at IS '本列自 production 取數的時間'",
    "COMMENT ON COLUMN evidence_snapshot.expires_at IS '快取到期時間；讀到過期視為 miss 並刪列'",
)

# downgrade 用：把註解清空（NULL＝無註解）
_TARGETS: tuple[str, ...] = tuple(s.split(" IS ")[0] for s in _COMMENTS)


def upgrade() -> None:
    """套用 9 張自建表的表註解與 118 個欄位註解。"""
    for stmt in _COMMENTS:
        op.execute(stmt)


def downgrade() -> None:
    """移除本支加上的所有註解（COMMENT ... IS NULL）。"""
    for target in _TARGETS:
        op.execute(f"{target} IS NULL")

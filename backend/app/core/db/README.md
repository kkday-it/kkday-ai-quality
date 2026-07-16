# app/core/db — 資料存取層（package）

本目錄依職責拆分為以下子模組；`__init__.py` barrel re-export 全公開函式，
外部 `from app.core import db` + `db.X()` **零改動**。子模組間相對 import（`from . import tables`），
共用態集中 `_shared.py`（無循環：domain 模組 → `_shared`；`export` → `problems`）。

| 模組 | 職責 |
|---|---|
| `tables.py` | SQLAlchemy schema + engine（`get_engine`/`set_engine`/`metadata`/`upsert`）；連線＝`config.env.database_url`。連線池由 env 調（`db_pool_size` 10 / `db_max_overflow` 20 / `db_pool_recycle` 1800 + `pool_pre_ping`；prejudge 64 執行緒共享，見 `_engine_kwargs`）。 |
| `source_registry.py` | 5 來源 → 表 routing SSOT（`SourceSpec`：table + natural_key + score/category/date 欄）。 |
| `_shared.py` | 共用：judgment 顯示標籤/信心閾值（`reload_judgment_cfg`：直讀專案靜態檔 config/ai_judge/judgment.json；2026-07-13 起 judgment 移出 RULE_CODES、不再 DB 版本化）、`_jg_join_cond`/`_jg_exists`（複合鍵 join）、`_vertical_codes`/`_scoped_spec`（商品垂直分類）、`fmt_datetime`；**判決 DTO SSOT**（`attribution_dto`：typed 欄 → 乾淨巢狀物件）。 |

## judgments 判決表結構（typed 欄 · 最佳架構）

一列 = 一條歸因，**全 typed scalar 欄**（無 JSONB blob）。判決表是查詢/聚合/篩選密集的分析核心且 schema 已穩定，故 storage 用 typed 欄（可直接 btree 索引、SQL 乾淨），巢狀物件屬呈現層於 API DTO 組（`_shared.attribution_dto`）。

**欄位**：關聯鍵 `finding_id`PK / `source` / `source_id` / `prod_oid`；傾向階段 `polarity` / `sentiment_score`（情緒分 1-5·LLM 讀原文細分夾區間 負1-2/中3/正4-5·與外部評論 sentiment 同尺度供對比表比對·null＝未判）/ `stage`；歸因 `l1_code` `l1_label` `l2_code` `l2_label`；信心 `conf_value` `conf_raw` `conf_tier`；內容 `summary` `evidence` `action`；元 `model` `is_primary` `judged_at`；人工覆核 `status`（new / auto_confirmed / confirmed / dismissed）`needs_review` `created_at`；操作者 audit `status_updated_by/at`（人工改狀態時記操作者 email + ISO 時間；系統自動路由不寫·重判依 finding_id 保留 status；**完整轉移軌跡在 judgment_history kind='status'**）。

- **寫入**：`schema.TicketFinding.to_columns()` 產出判決 payload 欄 + `findings._finding_values` 補關聯/人工欄（殘留/legacy 欄不入庫）。
- **查詢**（GROUP BY / FILTER / SORT）：直接 `jg.c.polarity == x` / `jg.c.l1_code` / `func.max(jg.c.conf_value)`，走 `idx_judgments_{polarity,stage,l1,l2,l3,sentiment,tier}` btree 索引（l2/l3/sentiment 為 taxonomy 子樹 + 情緒篩選熱路徑）。
- **API DTO**：`_shared.attribution_dto(row)` 組乾淨巢狀物件 `{polarity, stage, l1/l2/l3:{code,label}, confidence:{value,raw,tier}, content:{summary,evidence,action}, model, notes_count, is_primary, status}`——一條形狀貫穿 DB→API→前端（前端 `Attribution` interface 對齊）。
- 遷移：`7c05d105e825`（先攤成 JSONB 分組）→ `85a7dea69f9d`（JSONB blob → typed 欄，最佳架構）。詳兩個 migration 檔頭 docstring。
| `users.py` | 帳號 + user_settings CRUD（`DuplicateEmailError`）。 |
| `rule_versions.py` | 判決規則版本化（judge_rule_versions；active/歷史/恢復默認/seed）。`RULE_CODES`＝product_vertical + source_mapping + prompt_polarity + prompt_C-1~6（僅涵蓋商品分類/上傳表頭校驗/判決 Prompt 三類，不含 judgment 靜態設定）。 |
| `ingest.py` | 批次（batches）+ 來源表批量寫入/讀取（`insert_source_batch`/`get_items_by_ids`）+ `init_db`。 |
| `findings.py` | judgments CRUD（`insert_finding`/`replace_source_findings`〔重判整組替換，keyword-only `params`/`job_id`/`triggered_by` 供同交易寫入判決歷史〕/`get_finding`/`update_finding_status`〔同值冪等·轉移記史〕/`batch_update_finding_status`〔批量覆核·單交易 diff〕+ 歸因備註）。 |
| `problems.py` | 統一問題列表（`_enrich_problem` + `_paged_fanout` 多歸因 fan-out + `list_problems`）。 |
| `prejudge_targets.py` | 初判/再判目標選取（`prejudge_target_ids`，stage 驅動 + 列表全維度篩選。表級（兩分支皆套）：星等/日期/關聯 oid/有無外部評論，SSOT＝`_shared.apply_table_filters`；判決級（僅已判分支）：傾向/信心分層/L1。與 list_problems 同一份語義）。 |
| `attribution.py` | 歸因概覽聚合（`attribution_overview` + `attribution_breakdown`）。 |
| `export.py` | 問題列表美化 xlsx 導出（1:N fan-out + review 級欄合併儲存格；polarity 整列底色正綠/中灰/負紅；行高顯式鎖定為排除長文欄（評論內容/商品名稱/方案名稱）後各欄所需高度；另附「歸因統計」圖表表，見 `export_stats.py`；`snapshot_model`＝輸出結果版本：內容/列傾向替換為該模型 judgment_history 最新快照〔`_adapt_snapshot`·覆核軸留空·未判過的評論排除·口徑寫統計表 A2〕；`compare_models`＝並排對比模型多選：基準右側每模型附一組 review 級欄「情緒·M/L1·M/L2·M」〔`_compare_cols`/`_compare_values`·值取該模型 `latest_snapshots`·鍵前綴 `cmp__{model}__*` 不撞 attr 級鍵故自動合併儲存格·未判/判為無問題該欄空白〕）。 |
| `export_stats.py` | 導出歸因統計（由 in-memory rows 直接算情緒傾向/L1/L2/判決分層/判決階段分佈，附「歸因統計」表；≤6 類圓餅、>6 類橫向長條）。所見即所得。 |
| `llm_usage.py` | AI 使用紀錄（llm_usage：per-call 寫入 + 消耗 dashboard 聚合 `llm_usage_overview`）。 |
| `judgment_runs.py` | 歸因歷史（judgment_runs：run 級——每次批量/選取/單筆重判一列；建檔/狀態回寫/終態統計 + 列表分頁 + `judgment_run_detail` 聚合 llm_usage per-stage 明細 + `any_judged` 重判判定）。 |
| `judgment_history.py` | 判決歷史（judgment_history：**評論級** append-only 事件流——kind=`judgment` 判決快照〔`insert_judgment_event` 於 replace_source_findings 同交易寫入 + FOR UPDATE 防並發；model+params+result_digest 全欄位嚴格去重，僅排 judged_at〕/ `status` 覆核轉移〔params 記 {to, changes:[{finding_id, from}]}〕/ `note` 評論級備註 / `failure` 判決失敗留痕〔`insert_failure_event` best-effort 獨立交易·params.error·失敗筆不落 judgments 的唯一痕跡·供前端查因 + prejudge_targets 隱式重撈上限 max_implicit_retries〕。補 judgments「刪+插」重判不留痕缺口，model 維度供多模型對比；migration `f2a8c4d61e93` 建表 + 回填既有已判評論初始快照〔created_at=judged_at·params.backfilled〕；`latest_snapshots(source, model)`＝每評論該模型最新快照〔PG DISTINCT ON·快照導出用·partial index `b5c7e91f3a26`〕+ `list_judgment_models()`＝歷來判過的模型清單〔judgments ∪ 快照 distinct·stub 排最後〕）。 |
| `prompt_sandbox_runs.py` | 歸因列表「Prompt 測試」沙盒歷史（`prompt_sandbox_runs`：一 run＝對 item_ids 逐筆跑使用者勾選的 prompt_ids 子集，`insert_sandbox_run`/`list_sandbox_runs`/`sandbox_run_detail`）。與 `judgments`/`judgment_history`（正式初判）完全分離；`log` 欄存 `run_log.read(job_id)` 快照（供事後回看完整 LLM log，run_log 本身純記憶體）。 |
| `datapack.py` | 全庫資料包導出/匯入核心（`TABLE_LOAD_ORDER` 13 表 SSOT / `SENSITIVE_TABLES` / `current_alembic_head` / `validate_datapack` 乾跑白名單校驗 / `load_datapack` 單交易 truncate-load+序列重置 / `build_datapack` 匯出 zip）。匯入只灌白名單表·`table.insert()` 綁定參數·零 SQL 拼接；CLI `scripts/tools/dump_datapack.py` 與匯出端點共用打包邏輯。 |

# app/core/db — 資料存取層（package）

本目錄依職責拆分為以下子模組；`__init__.py` barrel re-export 全公開函式，
外部 `from app.core import db` + `db.X()` **零改動**。子模組間相對 import（`from . import tables`），
共用態集中 `_shared.py`（無循環：domain 模組 → `_shared`；`export` → `problems`）。

| 模組 | 職責 |
|---|---|
| `tables.py` | SQLAlchemy schema + engine（`get_engine`/`set_engine`/`metadata`/`upsert`）；連線＝`config.env.database_url`。連線池由 env 調（`db_pool_size` 10 / `db_max_overflow` 20 / `db_pool_recycle` 1800 + `pool_pre_ping`；prejudge 64 執行緒共享，見 `_engine_kwargs`）。 |
| `source_registry.py` | 5 來源 → 表 routing SSOT（`SourceSpec`：table + natural_key + score/bd_tag/date 欄 + `header_aliases`）；`header_column_map(source)` 為「上傳表頭 → DB 欄名」的唯一宣告（恆等 + mixpanel `$`/大寫別名），校驗端與寫入端共用。 |
| `_shared.py` | 共用：初判/判決顯示標籤/信心閾值（`reload_pipeline_cfg`：直讀專案靜態檔 config/ai_judge/prejudge.json＋verdict.json 合併；非 DB 版本化）、`_jg_join_cond`/`_jg_exists`（複合鍵 join）、`_vertical_codes`/`_scoped_spec`（商品垂直分類）、`fmt_datetime`；**初判 DTO SSOT**（`attribution_dto`：typed 欄 → 乾淨巢狀物件）；**wire 投影 SSOT**（`select_wire`：每欄 `.label(c.key)`，見下方「DB 欄名 vs Python key」）。 |
| `schema_bootstrap.py` | 啟動時的 schema 對齊單一入口（`docker-entrypoint.sh` 直接 `python -m` 呼叫）：判定 `fresh`/`ok`/`adopt`/`stamp:X`/`abort:X` 五種狀態後一律 `alembic upgrade head`，squash 相容表＝`SQUASHED_REVISIONS`。 |
| `settings_store.py` | 全項目共享設定（`setting_master` 表·單例 `__global__` row）讀寫（`load_settings_row`/`save_settings_row`）。 |
| `rule_versions.py` | 初判規則版本化（`judge_rule_version_lst`；active/歷史/恢復默認/seed）。`RULE_CODES`＝bd_tag_vertical + source_mapping + prompt_polarity + prompt_C-1~6（僅涵蓋商品分類/上傳表頭校驗/初判 Prompt 三類，不含 judgment 靜態設定）。 |
| `ingest.py` | 批次（`upload_batch_tbl`）+ 來源表批量寫入/讀取（`insert_source_batch`/`get_items_by_ids`）+ `init_db`（**僅測試夾具用**：app 啟動與容器啟動都不再 create_all，schema 單一走 alembic）。 |
| `findings.py` | `attribution_tbl` CRUD（`insert_finding`/`replace_source_findings`〔重新初判整組替換，keyword-only `params`/`job_id`/`triggered_by` 供同交易寫入歸因歷史〕/`get_finding`）。整組替換是**無承接**的：歸因列沒有人工可改欄位，故刪除前不撈舊值回填。 |
| `qc_evidence.py` | **production 訂單佐證唯讀查詢層**（訂單佐證閉環）：7 表 allow-list JSONB 投影點查（PII 欄位永不投影＋tests 斷言鎖定）、**拆欄快照快取**落本地 PG `evidence_snapshot_tbl` 表（PK=order_oid、一訂單一列、order 6h TTL 懶清理；ID/純量各自成欄、商品/規格/方案內容各自獨立 jsonb 欄，可直接對 DB grid 核對；不入 datapack）、讀出後在 `_assemble_tree()` 組裝成樹狀分組物件（order_summary/supplier_info/product_info/item_info/package_info/meta）供 API 消費、in-process single-flight、熔斷器（連續失敗整批降級）、`resolve_credentials_any()`（env 服務帳號→當前 user→**全庫任一 production** 三層 fallback：佐證團隊共享唯讀不綁個人設定）。⚠️ 過渡管道＝QC 共用 snapshot；終態＝SA/SD 專用 replica+服務帳號（切 env 即換，零改碼）。 |
| `problems.py` | 統一問題列表（`_enrich_problem` + `_paged_fanout` 多歸因 fan-out + `list_problems`）。 |
| `prejudge_targets.py` | 初判/再判目標選取（`prejudge_target_ids`，stage 驅動 + 列表全維度篩選。表級（兩分支皆套）：星等/日期/關聯 oid/有無外部評論，SSOT＝`_shared.apply_table_filters`；初判級（僅已初判分支）：傾向/信心分層/L1。與 list_problems 同一份語義）。 |
| `attribution.py` | 歸因概覽聚合（`attribution_overview` + `attribution_breakdown`）。 |
| `export.py` | 問題列表美化 xlsx 導出（⚠️ `item_ids`〔前端勾選〕下推至 SQL `natural_key IN (…)`——不下推的話選 20 筆也得先撈全表再記憶體過濾，實測 57s→0.4s；`review_tbl`/`conversation_tbl` 各有專屬版面 `_EXPORT_LAYOUTS`〔欄定義/分組/凍結欄數〕，欄寬於 `_style_header_grouped` 後覆寫回指定值、讓長表頭改用換行，否則會被撐到「表頭單行放得下」而凍結區近 200 字元寬）（1:N fan-out + review 級欄合併儲存格；資料表雙層表頭〔`_grouped_header_spans`/`_style_header_grouped`：列 1＝分類群組合併儲存格＋配色（原始反饋/訂單商品資料/AI 初判結果/人工判決，每個 `compare_models` 對比模型各自一色）、列 2＝具體欄位＋篩選箭頭，資料改自列 3〕；polarity 整列底色正綠/中灰/負紅；行高顯式鎖定為排除長文欄（評論內容/商品名稱/方案名稱）後各欄所需高度；L1/L2 合併為單一「歸因分類」欄〔`_taxonomy_text`·同格換行：上行 C-N 域名、下行 C-N-M 細項·只判到 L1 時單行；L1 的 C-N 由 `_domain_cn_map` 從 prompt id 派生，因 `l1_code` 只存機器值〕；另附「分類統計」圖表表（見 `export_stats.py`）與「**Prompts**」工作表〔`_append_prompts_sheet`：7 支初判 prompt active 版本快照·版本 meta 取 `list_rule_meta`·全文 DB active 優先/檔案回退·初判溯源〕；`snapshot_model`＝輸出結果版本：內容/列傾向替換為該模型 `attribution_event_lst` 最新快照〔`_adapt_snapshot`·判決軸留空·**該模型未初判過的評論保留資料列、判定欄留白**（不整列排除，導出筆數與列表總數一致）·口徑寫統計表 A2〕；`compare_models`＝並排對比模型多選：基準右側每模型附一組 review 級欄「情緒·M/L1·M/L2·M」〔`_compare_cols`/`_compare_values`·值取該模型 `latest_snapshots`·鍵前綴 `cmp__{model}__*` 不撞 attr 級鍵故自動合併儲存格·未初判/判為無問題該欄空白〕）。 |
| `export_stats.py` | 導出分類統計（由 in-memory rows 直接算情緒傾向/L1/L2/信心分層/初判階段分佈，附「分類統計」表；≤6 類圓餅、>6 類橫向長條）。所見即所得。 |
| `llm_usage.py` | AI 使用紀錄（`llm_usage_lst`：per-call 寫入 + 消耗 dashboard 聚合 `llm_usage_overview`）。⚠️ `stage` 才是「這次呼叫由誰驅動」（polarity / C-1~C-6 / pack_* / prompt_debug / prompt_debug_batch / prompt_revise…）；`feedback_source_code`（wire 名 `source`）只放 5 個反饋來源 code，調試台與 AI 改寫等非來源驅動的呼叫留空。 |
| `prejudge_runs.py` | 歸因歷史（`prejudge_run_tbl`：run 級——每次批量/選取/單筆重新初判一列；建檔/狀態回寫/終態統計 + 列表分頁 + `prejudge_run_detail` 聚合 `llm_usage_lst` per-stage 明細 + `any_judged` 重新初判判定）。 |
| `attribution_history.py` | 歸因歷史（`attribution_event_lst`：**評論級** append-only 事件流——kind=`prejudge` 初判快照〔`insert_prejudge_event` 於 replace_source_findings 同交易寫入 + FOR UPDATE 防並發；model+params+result_digest 全欄位嚴格去重〕/ `note` 評論級備註 / `failure` 初判失敗留痕〔`insert_failure_event` best-effort 獨立交易·params.error·失敗筆不落 `attribution_tbl` 的唯一痕跡·供前端查因 + prejudge_targets 隱式重撈上限 max_implicit_retries〕/ `router_shadow` 域路由影子比對留痕〔domain_router.report_shadow best-effort·params 記 {candidates, hit, missed, probs}·供持續量測路由召回；**不進使用者時間軸**，`list_attribution_history` 以 `_USER_VISIBLE_KINDS` 白名單擋下〕。補 `attribution_tbl`「刪+插」重新初判不留痕缺口，model 維度供多模型對比；建表、回填既有已初判評論初始快照〔params.backfilled〕與其 partial index 原本各為獨立 revision，**已併入 baseline v2 `94e60400715b`**，script 目錄查不到那些 id 是正常的；`latest_snapshots(source, model)`＝每評論該模型最新快照〔PG DISTINCT ON·快照導出用·走 partial index〕+ `list_prejudge_models()`＝歷來判過的模型清單〔`attribution_tbl` ∪ 快照 distinct·stub 排最後〕）。 |
| `datapack.py` | 全庫資料包導出/匯入核心（`TABLE_LOAD_ORDER` 12 表 SSOT / `SENSITIVE_TABLES` / `current_alembic_head` / `validate_datapack` 乾跑白名單校驗 / `load_datapack` 單交易 truncate-load+序列重置 / `build_datapack` 匯出 zip）。匯入只灌白名單表·`table.insert()` 綁定參數·零 SQL 拼接；CLI `scripts/tools/dump_datapack.py` 與匯出端點共用打包邏輯。⚠️ `evidence_snapshot_tbl` 是可重建的快取，刻意不入包（故 12 表 ≠ metadata 的 13 張表）。 |

## migration 鏈現況（2026-08-04 squash 後，改 migration 前必讀）

**鏈能從真正全空庫跑通到 head，且啟動路徑只有一條。**

- **baseline**＝`94e60400715b_baseline_v2_squash_2026_08_04.py`（`down_revision=None`），取代 `4ac23d6d20b4` 起的 15 支 migration。**以顯式 `op.create_table` × 17 定義，不用 `metadata.create_all()`** —— 這是與前一版 baseline 的關鍵差異：`create_all` 型 baseline 建出的是「執行當下的 `tables.py`」而非「本 revision 當下的 schema」，語意會隨 code 漂移。2026-08-04 實測證實那正是鏈斷的根因（表改名為 `review_tbl` 後，baseline 直接建出新表名，其後引用 `product_reviews` 的 migration 全部撞 `UndefinedTable`），而雙軌分流讓空庫從不跑鏈，因此壞了很久沒人發現。
- **啟動路徑單一化**（見 `backend/docker-entrypoint.sh` → `app/core/db/schema_bootstrap.py`）：一律 `alembic upgrade head`，不再有「空庫 create_all+stamp／既有庫 upgrade」雙軌。雙軌本身就是漂移溫床——兩條路徑各自造 schema，卻沒有任何東西比對它們。判斷邏輯抽成 Python 模組（而非 bash heredoc）才可單元測試，五種狀態見 `tests/test_schema_bootstrap.py`：`fresh` / `ok` / `adopt`（有表無版本紀錄，須 stamp 不可重跑 DDL）/ `stamp:X`（squash 相容）/ `abort:X`（認不得且未登記 → 擋下啟動）。
- **squash 對既有環境的處理**：`schema_bootstrap.SQUASHED_REVISIONS` 登記 `a1d7e3f92b64 → 94e60400715b`；既有環境的 `alembic_version` 認不得已刪除的 revision 時，據此 `command.stamp(..., purge=True)` 重新蓋章再增量升級（`purge` 必要，否則 alembic 仍會嘗試解析那個已不存在的當前版本）。
  ⚠️ **此表刻意不與 `datapack.LEGACY_COMPATIBLE_HEADS` 共用**：後者回答「舊資料包欄位形狀還相容嗎」，目標即使後來又被 squash 掉仍有意義；前者的目標**必須是 script 目錄裡現存的 revision**，否則 stamp 直接拋 `Can't locate revision`。共用曾實際踩到這個坑，現由 `test_squash_targets_all_exist_in_script_directory` 守住。
  ⚠️ **只登記 squash 前的最終 head**，不登記中間版本——停在中間 revision 的環境 schema 其實落後，自動 stamp 會謊稱最新並永久漏掉 DDL。
- **兩條護欄**（`backend/tests/`）：`test_schema_parity.py` 實際建兩個空庫、分別跑 alembic 鏈與 `metadata.create_all`，逐欄逐索引比對（這是真正的防漂移護欄）；`test_all_tables_have_create_migration.py` 為字面掃描，是前者的子集，待其穩定後可整支退役。
- **新增表時**：務必在對應 migration 寫真實 `create_table`；baseline 只涵蓋 squash 當下已存在的表。
- **下次 squash 的時機**：table 結構變化收斂穩定時才做；**平常一律照常開新 revision 檔**——每次 squash 都要重做整套收尾（`SQUASHED_REVISIONS` 登記＋回頭檢查舊條目目標是否還活著、`LEGACY_COMPATIBLE_HEADS` 登記、`seed.sql.gz` 重產、測試調整），頻繁做的成本遠高於讓檔案數量正常累加。

## 開／刪索引一律加 CONCURRENTLY（規範第一章 · 新增索引時必讀）

規範明訂「**為避免 table lock 請在開/刪 index 都要加上 concurrently**」。

```python
# ✅ 正確：CONCURRENTLY 不能在交易內執行，須開 autocommit_block
def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY idx_attribution_tbl_polarity ON attribution_tbl (polarity)"
        )
```

前置條件已備妥：`alembic/env.py` 設了 `transaction_per_migration=True`（Phase 0 加的），
`autocommit_block()` 才能正常運作。

⚠️ **既有 migration 未使用 CONCURRENTLY**（`a8e5c31d0f62` 的 5 個 `CREATE UNIQUE INDEX`）。
那是刻意的取捨：① 那支是**一次性的改名遷移**，執行時表最大 5.8 萬列、鎖定時間毫秒級
② `ALTER INDEX RENAME` 本來就不需要 CONCURRENTLY（不重建索引）
③ 已套用的 migration 不回頭改寫——那正是 baseline v1 把鏈弄斷的反模式。
**但往後在既有表上新增索引，一律照上方寫法**，尤其 production 的大表。

## DB 欄名 vs Python key（DDL 對齊後的雙軌 · 動手前必讀）

DDL 規範對齊後，**31 個欄位的 DB 名與 Python key 刻意不同**——DB 用規範名（`feedback_source_code`），
Python 與 wire 維持原名（`source`）。宣告方式：`Column("feedback_source_code", key="source", …)`。

**為什麼要這樣**：規範管的是 DataBase，不是 API。全面改 wire key 會讓前端上百處跟著動、且對外契約
無故變更；用 `key=` 把改名收斂在資料層，前端零改動（由 `tests/test_wire_contract.py` 凍結快照證明）。

⚠️ **最大的坑：`key=` 只在「組查詢」時生效，「讀結果」時 SQLAlchemy 的 result mapping 一律用 DB 欄名。**

| 情境 | 寫法 | 結果鍵 |
|---|---|---|
| 組查詢條件 | `t.c.source == x` | —（正確渲染成 `feedback_source_code`）|
| **全欄直出** | ~~`select(t)`~~ → **`select_wire(t)`** | 用 `select_wire` 才是 Python key |
| **顯式投影** | ~~`select(t.c.source)`~~ → **`select(t.c.source.label("source"))`** | 不 label 就是 DB 名 |
| 寫入 | `insert(t).values(source=…)` | —（kwargs 依 key 解析，正確）|
| `upsert()` 的 `pk` | 傳 **key**（內部轉 Column 物件）| — |

**別名欄一覽**（`c.name != c.key`）：可用
`python -c "from app.core.db import tables as T; [print(f'{t.name}.{c.name} ← .c.{c.key}') for t in T.metadata.tables.values() for c in t.columns if c.name != c.key]"` 隨時查。

**datapack 三處必須同軸**（皆用 `col.key`）：匯出 `dump_table_ndjson`、驗證 `columns.keys()`、
匯入 `_coerce_row`。唯一例外是 `_SEQUENCE_TABLES` 用 `col.name`——它餵給 `pg_get_serial_sequence()`，
那是 DB 層函式只認 DB 名。不變式由 `test_datapack_uses_column_key_consistently` 守住；
三處一旦分歧，資料包會**通過驗證卻整表靜默匯入全 NULL**。

## 欄位註解（COMMENT ON · DDL 規範）

**13 張表的表註解與 126 個欄位註解的唯一真相源＝`tables.py` 的 `Table(comment=)` / `Column(comment=)`**，
由四支 migration 依序落到 PG catalog（`pg_description`）：`f3d92a7c48be`（當時 9 張自建表 + 118 欄）
→ `a8e5c31d0f62`（改名；comment 綁 attnum，改名不會弄丟）→ `b6f04a2e7d31`（補改名時新增的 serial PK
與審計欄）→ `d9c173be5f8a`（5 張來源鏡像表的表註解）。
改註解＝改 `tables.py` + 開新 revision，**不回頭改既有 migration**——那支的 SQL 是
產生當下的凍結快照，理由同 baseline v2（migration 一旦讀執行期的 code，「這支 revision 做了什麼」
就不再可重現）。

⚠️ **`f3d92a7c48be` 的檔頭寫「9 張自建表 / 118 欄」是它產生當下的事實**，別拿來對當前狀態——之後
`e2a91c47d0b3` 退場了 `prompt_debug_review_tbl`（自建表 9→8），`b6f04a2e7d31` 又補了改名新增的欄位。

**5 張來源鏡像表只有表註解、沒有欄位註解**（`review_tbl` / `conversation_tbl` / `freshdesk_ticket_tbl` /
`app_feedback_tbl` / `mixpanel_tracker_tbl`）：欄位註解仍在豁免範圍（欄名逐欄對齊上游取數 SQL 的輸出
契約，語義說明應隨上游文件而非在此複述）。故 126 欄註解全部落在 8 張自建表上。

查當前狀態：`SELECT count(*) FROM pg_description d JOIN pg_class c ON c.oid=d.objoid
JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public';`（應為 139＝13 表 + 126 欄）

## attribution_tbl 初判表結構（typed 欄 · 最佳架構）

一列 = 一條歸因，**全 typed scalar 欄**（無 JSONB blob，`summary` 的語系 map 除外）。初判表是查詢/聚合/篩選密集的分析核心且 schema 已穩定，故 storage 用 typed 欄（可直接 btree 索引、SQL 乾淨），巢狀物件屬呈現層於 API DTO 組（`_shared.attribution_dto`）。

**欄位（23 欄）**：
- **主鍵**：`attribution_oid`（serial，DDL 規範的 `名詞_oid`）
- **關聯鍵**：`feedback_source_code` / `source_id`
- **初判組**：`polarity` / `sentiment_score`（情緒分 1-5·LLM 讀原文細分夾區間 負1-2/中3/正4-5·與外部評論 sentiment 同尺度供對比表比對·null＝未初判）/ `prejudge_stage`
- **歸因**：`l1_code` `l1_label` `l2_code` `l2_label`
- **信心**：`conf_value` `conf_raw` `conf_tier`
- **內容**：`summary`（jsonb 語系 map）`evidence` `recommended_action`
- **簿記**：`model` / `is_primary`（多歸因主歸因旗標）/ `is_auto_accepted`（信心達 auto_accept 門檻且非 needs_review 階段時由 `prejudge._route_auto_accept` 設 true）
- **審計四欄**：`create_user` `create_date`（初判落庫時間＝唯一時間源）`modify_user` `modify_date`

**唯一鍵**＝`idx_attribution_tbl_unique01 (feedback_source_code, source_id, l1_code, l2_code)`——一則反饋在同一個 L1/L2 面向上只會有一條歸因，重新初判時 upsert 靠這組自然鍵對得上舊列；身分職責則由 `attribution_oid` 承擔。

歸因列**沒有任何人工可改的欄位**——一列的全部內容都由初判產生，重新初判即整組替換。評論級的人為輸入（備註）與歷次初判軌跡都在 `attribution_event_lst`（綁 `(feedback_source_code, source_id)` 這個跨重新初判穩定的鍵；歸因列本身會整批換掉）。

- **寫入**：`schema.TicketFinding.to_columns()` 產出初判 payload 欄 + `findings._finding_values` 補關聯鍵與簿記欄。
- **查詢**（GROUP BY / FILTER / SORT）：直接 `jg.c.polarity == x` / `jg.c.l1_code` / `func.max(jg.c.conf_value)`，走 `idx_attribution_tbl_{polarity,prejudge_stage,l1_code,l2_code,sentiment_score,conf_tier}` btree 索引（l2/sentiment 為 taxonomy 子樹 + 情緒篩選熱路徑），join/EXISTS 走 `idx_attribution_tbl_mix01`（feedback_source_code, source_id 複合）。
- **API DTO**：`_shared.attribution_dto(row)` 組乾淨巢狀物件 `{attribution_oid, polarity, sentiment_score, stage, l1/l2:{code,label}, confidence:{value,raw,tier}, content:{summary,evidence,action}, owner, model, is_primary, is_auto_accepted}`——一條形狀貫穿 DB→API→前端（前端 `Attribution` interface 對齊）。此形狀由 `tests/test_wire_contract.py` 凍結，改動任一鍵會立刻紅燈。

> ⚠️ **DB 欄名 ≠ Python/wire 名**：`feedback_source_code` 在 Python 端與 wire 上都叫 `source`（`Column(..., key="source")`），`recommended_action` 對應 `action`。`key=` 只影響查詢構建，`mappings()` 回的仍是 DB 欄名——投影一律走 `_shared.select_wire()`（每欄 `.label(key)`），詳見該函式 docstring。

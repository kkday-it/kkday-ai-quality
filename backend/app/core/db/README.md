# app/core/db — 資料存取層（package）

原單一 `db.py`（~1400 行）按職責拆為子模組；`__init__.py` barrel re-export 全公開函式，
外部 `from app.core import db` + `db.X()` **零改動**。子模組間相對 import（`from . import tables`），
共用態集中 `_shared.py`（無循環：domain 模組 → `_shared`；`export` → `problems`）。

| 模組 | 職責 |
|---|---|
| `tables.py` | SQLAlchemy schema + engine（`get_engine`/`set_engine`/`metadata`/`upsert`）；連線＝`config.env.database_url`。 |
| `source_registry.py` | 5 來源 → 表 routing SSOT（`SourceSpec`：table + natural_key + score/category/date 欄）。 |
| `_shared.py` | 共用：judgment.json 標籤/信心閾值、`_jg_join_cond`/`_jg_exists`（複合鍵 join）、`_vertical_codes`/`_scoped_spec`（商品垂直分類）、`fmt_datetime`。 |
| `users.py` | 帳號 + user_settings CRUD（`DuplicateEmailError`）。 |
| `rule_versions.py` | 判決規則版本化（judge_rule_versions；active/歷史/恢復默認/seed）。 |
| `ingest.py` | 批次（batches）+ 來源表批量寫入/讀取（`insert_source_batch`/`get_items_by_ids`）+ `init_db`。 |
| `findings.py` | judgments CRUD（`insert_finding`/`replace_source_findings`/`list_findings`/`list_products`）。 |
| `problems.py` | 統一問題列表（`_enrich_problem` + `_paged_fanout` 多歸因 fan-out + `list_problems`）。 |
| `prejudge_targets.py` | 初判/再判目標選取（`prejudge_target_ids`，stage 驅動）。 |
| `attribution.py` | 歸因縱覽聚合（`attribution_overview` + `attribution_breakdown`）。 |
| `export.py` | 問題列表美化 xlsx 導出（1:N fan-out + review 級欄合併儲存格）。 |

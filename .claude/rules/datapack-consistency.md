---
paths:
  - "**/tables.py"
  - "**/alembic/versions/**"
  - "**/migrations/**"
  - "**/datapack.py"
  - "**/dump_datapack.py"
  - "**/import_jobs.py"
---

# 資料包（datapack）匯出/匯入數據一致性（改 DB schema / 初判規則內容形狀時必查）

**核心原則：資料包＝全庫可攜快照，任何改動 DB 結構或改變已落庫 JSONB 內容形狀的邏輯，都要同步考慮匯出/匯入是否還能正確往返，不能只顧當下 schema 改動本身。** 踩過的坑：`judge_rule_versions.content`（Prompt-as-Source 重構期間同一天多次 reseed / 形狀調整）— 資料包本身 round-trip 機制沒壞，但「匯出時機」與「當前前端消費端預期的內容形狀」不一致時，會靜默呈現空白而非報錯（見 `datapack-import-stale-store-fix-2026-07-15` memory）。

## 改動 → 必查清單

| 改了什麼 | 必須確認 / 同步處理 |
|---|---|
| **新增 DB 表**（`tables.py` 新 `Table(...)`）| 是否該進 `backend/app/core/db/datapack.py` 的 `TABLE_LOAD_ORDER`（可攜業務資料 → 加；純衍生/快取表 → 明確不加並在該處註解為何）；若含帳號/機密 → 加進 `SENSITIVE_TABLES` |
| **表有 autoincrement PK**（新表或既有表新增）| 若會被 `load_datapack` truncate-then-load 整表覆蓋，需加進 `_SEQUENCE_TABLES`（否則還原顯式 id 後續 insert 會主鍵衝突） |
| **刪除 DB 表 / 欄位**（呼應 `feature-retirement.md`）| `TABLE_LOAD_ORDER` / `_SEQUENCE_TABLES` 同步移除該表；欄位刪除本身由 SQLAlchemy `Table.columns` 驅動 `_coerce_row`/`validate_datapack`，通常自動一致，但**跨 schema_version 的舊資料包會被硬拒**（`current_alembic_head()` 比對），這是刻意設計，不需另補相容邏輯 |
| **改變已落庫 JSONB 欄位的內部形狀**（如 `judge_rule_versions.content` 的 key 結構、任何前端依賴特定 key 解析的欄位）| schema_version 比對**攔不住這種改動**（欄位型別沒變，只是語意變了）——若此類欄位有多個 reseed / 形狀迭代版本並存於同一 alembic head 之下，**當輪需一併確認**：① 前端消費端對「內容形狀不符預期」是否有顯性防禦（空狀態提示，而非靜默空白，呼應 `StateGuard` 的 `empty` prop 用法）② 是否需要在讀取端做形狀相容轉換 ③ 舊資料包匯入後是否需要引導使用者跑 `resetDefault`/`reset_rule_default` 重新對齊 |
| **`datapack.py` / `dump_table_ndjson` / `load_datapack` 邏輯本身**（型別轉換、chunk 大小、序列重置等）| CLI（`scripts/tools/dump_datapack.py`）與 API 匯出端點（`admin_import.py`）共用同一組函式（`TABLE_LOAD_ORDER`/`build_datapack`/`resolve_export_tables`）——**禁止**為其中一端另寫平行邏輯，改一處兩端同步生效 |
| **匯入後前端狀態失效**（新增/修改任何 Pinia store 讀取 datapack 涵蓋的表）| 目前策略是匯入完成後整頁 `location.reload()`（見 `DataImportPanel.vue`），**不需要**額外幫新 store 補 refetch；若之後改成細粒度 store 失效，新增 DB-backed store 時要一併掛上失效清單 |

## 驗證（改動涉及上表任一項時必跑）

匯出 → 匯入 round trip 至少跑一次（容器內：走 `/api/admin/export`+`/api/admin/import` 或 CLI `dump_datapack.py`），確認新/改動欄位在匯入後值仍正確，而非只看 `pytest` 綠燈（現有測試未覆蓋 datapack 完整 round trip，見稽核記錄）。

## 反向氣味（出現即代表沒顧到一致性）

- 新增表但沒進 `TABLE_LOAD_ORDER` 討論（沒加、也沒註解為何不加）→ 補
- 新增 autoincrement 表卻沒進 `_SEQUENCE_TABLES` → 補（否則匯入後續寫入會主鍵衝突）
- 改了某 JSONB 欄位形狀，前端讀取端遇到舊形狀/空值時是「靜默空白」而非「顯性空狀態/報錯」→ 補防禦（`StateGuard` 等三態元件必須傳 `empty`）
- `datapack.py` 改動只測了 API 端點，沒確認 CLI `dump_datapack.py` 是否仍呼叫得通（兩端共用函式簽名是否被改動波及）→ 補

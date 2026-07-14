---
paths:
  - "**/alembic/versions/**"
  - "**/tables.py"
  - "**/migrations/**"
---

# 功能退場機制（移除舊功能 → 全棧零殘留）

**核心原則：退役即徹底，不留痕跡。** 移除一個功能時，它在**代碼邏輯 / DB schema / 前端 / 配置 / 文件 / 註釋 / 測試**各層的存在都要一併清乾淨——不留 tombstone 註釋、不留「歷史相容」的死欄、不留死 config key、不留 orphan import、不留死文件引用。**後面若再需要，直接在對應層重新補到位**，而非預留半殘骨架「以防未來」。

## 退場前：盤點消費點（必做，先量測再動刀）

grep 該 feature 的所有 symbol（函式 / 欄位 / 端點 / 元件 / 型別 / config key）across `backend/app` + `frontend/apps/console/src` + `config/**` + `**/*.md` + tests。逐一分類：

- **only-for-this-feature** → 移除
- **shared（他處也在用）** → **保留，勿誤刪**。例：`taxonomy-cascade`/`getTaxonomyCascade` 被歸因列表篩選共用，即使某消費者退役也要留。

## 全棧清退清單（逐層核對，缺一層即未清乾淨）

1. **後端代碼**：函式 / API 端點 / Pydantic schema / service 邏輯。移除後查 **orphan imports**——只服務被刪功能的 import 一併清（踩過：`UploadFile`/`File`、`JSON_HEADERS`）。
2. **DB schema**（有落庫才需要）：
   - `tables.py` 移除 `Column` / `Index`
   - alembic drop migration：`DROP ... IF EXISTS`（冪等）；**drop 前 `pg_dump` 備份**（`~/kkday-backups/`）；downgrade 加回 nullable（不還原資料）
   - 連帶清：DTO（`attribution_dto`）/ 寫入端（`findings._finding_values`、`to_columns`）/ 讀取端（select 欄清單、`_PROBLEM_COLS`）/ 去重 digest / 索引，皆去該欄
   - ⚠️ 容器內 `alembic revision` 生成的檔用**容器時區 mtime**（比 host 早數小時，`ls -t` 會誤排）；檔仍落 host（bind mount）
3. **barrel**：`__init__.py` / `index.ts` 移除 re-export（**import 段 + `__all__` 兩處都要**）
4. **配置**：移除死 config key（**grep 確認 0 consumer 才刪**；判決領域改值需重啟後端）
5. **前端**：元件整檔刪 / 型別 interface 欄 / `.api.ts` function / composable / store / template 使用 / 路由；移除後 `vue-tsc` 會抓 orphan import 與斷型別
6. **測試**：刪該 feature 專屬 test 檔；修 assert 到被刪欄/回傳鍵的 test
7. **文件（docs-sync 鐵律，見 `docs-sync.md`）**：README（模組 / 資料夾 / 根）/ API 一覽表 / 文件引用——**刪除即清引用**，勿留死連結 / 幽靈欄位
8. **註釋（不留痕跡）**：
   - 刪退役 tombstone：「X 已退役 / 於 DATE 退役 / legacy X / 取代已移除的 Y / 恆 NULL 保留供歷史相容」
   - 改寫成**描述現狀**、非退役史。例：「原 JSON 樹已退役——判準改走 prompt」→「判準走 prompt」
   - ⚠️ **保留活碼語義的 `legacy`**：如 `_migrate_legacy`（舊設定格式遷移函式）、`_derive_stage` 的 legacy 空欄 fallback、「過濾已刪分組」（runtime）——那是**現行相容邏輯**，不是退役痕跡

## 驗證（收尾必跑，缺一不可標完成）

`pytest` 全綠 ＋ `vue-tsc` / `eslint` 零錯 ＋ 容器 `alembic upgrade head` ＋ 判決煙霧（DTO / breakdown 不含該欄）＋ **最終殘留 grep 全清**（該 feature 所有 symbol 在 `backend/app` + `frontend/src` 命中 0，僅「shared 保留」與「活碼 legacy」除外）。

## 反向氣味（出現即代表沒清乾淨）

- 保留恆 NULL / 恆 0 / 恆空的「歷史相容」欄 → 拔掉（要用再建 migration）
- 「未來可能用到」預留的半殘 scaffolding / 空 key / 佔位欄 → 刪
- 「X 已於 DATE 退役」breadcrumb 註釋 → 刪
- README / API 表仍列已刪端點 / 欄位 / 模組 → 清
- 被刪 symbol 的 orphan import 未清 → 清
- 只判到某層就收手（如刪了元件但漏 api function、刪了欄但漏 DTO）→ 補齊全棧

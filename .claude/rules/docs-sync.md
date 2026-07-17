---
paths:
  - "**/*.py"
  - "**/*.ts"
  - "**/*.tsx"
  - "**/*.vue"
  - "config/**"
  - "constants/**"
---

# 文檔同步規則（改邏輯 → 同步文檔，編輯 code/config 時載入）

**核心鐵律：任何邏輯 / 結構 / 契約改動，完成後必須同步更新所有相關文檔——文檔與 code 不得漂移。** 這是硬性收尾步驟，非可選。

## 何時必須更新哪份文檔（改動 → 對應文檔）

| 改了什麼 | 必須同步更新 |
|---|---|
| **API 端點**（新增/刪除/改路徑/改參數）| 根 `README.md` API 一覽表 + `backend/app/api/README.md` + Swagger docstring |
| **核心流程 / 架構**（來源表、初判管線、資料流）| 根 `README.md` 核心流程 + 架構要點 |
| **資料夾結構 / 模組**（移檔、拆包、新增/刪除模組）| 該資料夾 `README.md` + 上層 `README.md` 結構表 + 受影響的 barrel/import 說明 |
| **`config/` `constants/` 檔案**（新增/刪除/改語義/改位置）| `config/README.md` / `constants/README.md` 對應條目 |
| **DB schema / 遷移**（表、欄、關聯鍵）| `backend/app/core/db/README.md` + 根 README 架構要點（若對外可見）|
| **判準 loader / 規則機制** | `backend/app/core/judge_config/README.md` |
| **前端 feature 結構 / 主要頁面** | `frontend/apps/console/src/features/README.md` |
| **腳本**（新增/刪除/移動）| `scripts/README.md` 表格 + 對應子夾 |
| **函式 / class 的用途或簽名** | 該檔 docstring / JSDoc（見 `python.md` / `typescript.md`）|

## 執行要求（強制）

1. **同一輪完成**：邏輯改動與文檔更新在同一 commit / 同一任務內完成，禁止「先改碼、文檔待補」。
2. **寫文檔前先核實 code**：文檔內容（檔案位置、函式名、端點路徑、model 欄位、config 檔清單）**一律以當前 code 為準逐一核對**，禁止憑記憶或舊文檔書寫（踩過坑：憑記憶寫錯 config 檔位置、提及已刪的 model、寫錯端點路徑）。
3. **刪除即清引用**：刪 code / 端點 / 檔案時，同步移除文檔中對它的引用（含 README 表格、連結、範例），避免死引用。
4. **過時即更新，非新增**：發現既有文檔描述已淘汰的架構（如舊端點、已移除概念）→ 更新或加「當前以 X 為準」指向，不是另寫一份平行文檔。
5. **連結有效性**：README 內部連結指向的檔案/目錄必須存在（改動後複查，勿留斷鏈）。

## 反向氣味（出現即停下補文檔）

- 改了 API 端點但 README API 表沒動 → 補
- 移動/拆分模組但資料夾 README 或上層結構表沒更新 → 補
- 刪了 code 但文檔還引用它（死引用）→ 清
- 文檔宣稱的檔案/函式/欄位在 code 找不到 → 以 code 為準修正文檔

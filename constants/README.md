# constants — 固定參照常數字典 SSOT

**固定參照**常數（enum / 代碼→文案字典 / 對照表），工程師維護、變動低頻、通常來自外部權威來源。
按**維度**分子資料夾。前後端**同讀同一份 JSON**：後端 `app.core.paths.CONSTANTS_DIR`，前端 `@constants` alias。

與 `config/` 的分工：`config/`＝**業務可調**（規則/閾值/清單）；`constants/`＝**固定字典**（代碼→文案）。
兩者皆禁在前後端各寫一份（見 `.claude/rules/config-and-hardcode.md`）。

## labels/
代碼 → 中文文案字典（如 `guide_lang` 導覽語系、`traveller_type` 旅客類型），源自 kkday-member-ci。
檔名 `<name>.constant.json`。

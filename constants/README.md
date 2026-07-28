# constants — 固定參照常數字典 SSOT

**固定參照**常數（enum / 代碼→文案字典 / 對照表），工程師維護、變動低頻、通常來自外部權威來源。
按**維度**分子資料夾。前後端**同讀同一份 JSON**：後端 `app.core.paths.CONSTANTS_DIR`，前端 `@constants` alias。

與 `config/` 的分工：`config/`＝**業務可調**（規則/閾值/清單）；`constants/`＝**固定字典**（代碼→文案）。
兩者皆禁在前後端各寫一份（見 `.claude/rules/config-and-hardcode.md`）。

## labels/
代碼 → 中文文案字典（如 `guide_lang` 導覽語系、`traveller_type` 旅客類型），源自 kkday-member-ci。
檔名 `<name>.constant.json`。

> `labels/` 檔案少屬**刻意設計**（固定字典本就低頻新增）——勿因檔少把本目錄
> 併進 `config/`；新維度比照既有慣例加子資料夾即可。

> ⚠️ 商品 BD 分工代碼 → PM/Vertical 對照**不在此目錄**：業務會調（BD 團隊調整分工/新增代碼），
> 已改走 `config/global/bd_tag_vertical.json`（DB 版本化規則 rule_code=`bd_tag_vertical`，
> 可在「配置」抽屜〔商品垂直分類 tab〕編輯/歷史/恢復默認），非本目錄的固定字典語意。

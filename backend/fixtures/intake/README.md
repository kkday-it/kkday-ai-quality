# intake 樣本資料（售前售後進線）

AI 法官 V3 第一管道（售前售後進線）的 BigQuery 查詢結果樣本，供 L1 接入 / adapter 開發與 golden 標註使用。

## ⚠️ 個資警示
真實匯出的 `*.csv`（如自 BigQuery 重新下載的完整資料）含**真實客戶對話、訂單/會員 ID、供應商 ID**，屬敏感個資：
- 已於 repo 根 `.gitignore` 忽略 `backend/fixtures/intake/*.csv`，**禁止 commit**（僅本機開發使用）。
- **例外**：`conversations_sample_30col.csv` 為**全合成**小量樣本（零真實客戶資料，欄位值皆為虛構 `SAMPLE_*`），已於 `.gitignore` 白名單允許 commit，供開發者對照新格式結構——若要用真實資料排查問題，另外自行下載匯出檔，勿覆蓋此檔。

## 檔案

| 檔名 | 來源 | 內容 |
|---|---|---|
| `conversations_sample_30col.csv` | 全合成範例（非真實資料）| 4 筆，涵蓋 4 種 `bucket` 值與 `[CHATBOT]`/`[真人]`/`⏎`/`‖` 對話格式範例，**可 commit** |

## 欄位（2026-07-24 起新版：30 欄扁平 CSV，逐字對應表欄名，含大寫 `PM`）
`session_oid, bucket, inbound_time, trip_stage, godate_diff, msg_handler_bucket, member_uuid, order_oid, order_mid, order_create_time, order_status_now, order_lang, go_date, order_price, order_profit, order_create_source_code, prod_oid, product_name, product_tz, vertical, bd_tag_cd, bd_tag, PM, product_category, supplier_oid, supplier_name, cs_tag_oid, cs_tag_name, user_message_count, conversation_full`

- `bucket` / `godate_diff` / `msg_handler_bucket` / `vertical` / `bd_tag_cd` / `PM`：皆為 **BigQuery 端預算完成的字面值**，後端僅照欄入庫 + 曝光，不做任何衍生計算。
- `bucket`：該 session 整體分桶——`transferred`（機器人轉真人）/ `chatbot_only`（純機器人）/ `human_supplier`（真人·供應商）/ `human_kkday`（真人·KKday）/ `human_other`（真人·其他）。
- `msg_handler_bucket`：處理方——`KKDAY` / `SUPPLIER`。
- `trip_stage`：行程階段——`Open Date` / `Pre-trip` / `Pre-trip Critical` / `D0` / `Post-trip`。
- `conversation_full`：單欄展平的對話全文——**段落**以 ` ‖ ` 分隔、段落起首 `[CHATBOT]`（機器人階段）或 `[真人]`（真人客服階段）標記；**段內輪次**以 ` ⏎ ` 分隔、輪次起首 `[ROLE]:`（`USER`/`BOT`/`KKDAY`/`SUP`）標記發話角色；輪次文字內原始換行以轉義字面 `\n` 表示（前端 `parseDialogue` 還原）。取代舊版 `chatbot_conversation`/`human_conversation` 雙欄 + `merge_fields` 合併機制。

## 對應規格
- SQL 出處：H2 內容治理規劃 Doc（`1MN_aLEzpIlsOM1G9IoGO1sXZ_eUPjXLldMicfqyrxmA`）
- 規格：Confluence「① 如何整合（V3）」

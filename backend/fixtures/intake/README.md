# intake 樣本資料（售前售後進線）

AI 法官 V3 第一管道（售前售後進線）的 BigQuery 查詢結果樣本，供 L1 接入 / adapter 開發與 golden 標註使用。

## ⚠️ 個資警示
本資料夾的 `*.csv` 含**真實客戶對話、訂單/會員 ID、供應商 ID**，屬敏感個資。
- 已於 repo 根 `.gitignore` 忽略 `backend/fixtures/intake/*.csv`，**禁止 commit**。
- 僅本機開發使用；如需共享，先去識別化（遮罩 `order_mid` / `aggregated_messages` 內個資）。

## 檔案

| 檔名 | 來源 | 內容 |
|---|---|---|
| `postsale_intake_sample.csv` | 售後進線 SQL（訂單訊息 + chatbot 聚合）| 10,324 筆，欄位見下 |

## 欄位（對應售後進線 SQL）
`session_oid, zendesk_ticket_id, session_create_date, order_oid, order_mid, sessionable_type, sessionable_id, prod_oid, session_direction, supplier_oid, msg_handler, aggregated_messages, prod_bd_tag_note, prod_name_zh_tw, order_profit`

- `sessionable_type`：`order_message` / `chatbot`
- `aggregated_messages`：多輪對話，角色 `KKday 客服` / `user` / `供應商`
- `msg_handler`：`KKDAY` / supplier

## 對應規格
- SQL 出處：H2 內容治理規劃 Doc（`1MN_aLEzpIlsOM1G9IoGO1sXZ_eUPjXLldMicfqyrxmA`）
- 規格：repo `docs/PLAN-V3-售前售後進線.md` 附錄、Confluence「① 如何整合（V3）」

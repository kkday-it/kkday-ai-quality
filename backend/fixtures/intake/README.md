# intake 樣本資料（售前售後進線）

AI 法官 V3 第一管道（售前售後進線）的 BigQuery 查詢結果樣本，供 L1 接入 / adapter 開發與 golden 標註使用。

## ⚠️ 個資警示
本資料夾的 `*.csv` 含**真實客戶對話、訂單/會員 ID、供應商 ID**，屬敏感個資。
- 已於 repo 根 `.gitignore` 忽略 `backend/fixtures/intake/*.csv`，**禁止 commit**。
- 僅本機開發使用；如需共享，先去識別化（遮罩 `order_mid` / `chatbot_conversation` / `human_conversation` 內個資）。

⚠️ 本資料夾現存 `*.csv` 樣本為**舊版表頭**（2026-07-15 前），已隨 `conversations` 表改版過時，僅供歷史參考；新版表頭請見下方「欄位」一節，實際樣本待重新匯出。

## 檔案

| 檔名 | 來源 | 內容 |
|---|---|---|
| `postsale_intake_sample.csv` | 售後進線 SQL（訂單訊息 + chatbot 聚合）| 10,324 筆，**舊版表頭**（見上方警示）|

## 欄位（對應售後進線 SQL，2026-07-15 起新版）
`session_oid, zendesk_ticket_id, session_date_tw, session_datetime_tw, order_mid, order_oid, order_lang, order_price_pay, order_profit, order_create_source_code, prod_oid, product_name, prod_name_zh_tw, prod_bd_tag_note, product_category, order_go_date, product_timezone, trip_stage, order_status, supplier_oid, supplier_name, msg_handler, review_score, review_content, cs_task_type_name, inbound_session_count, conversation_type, user_msg_count, agent_msg_count, chatbot_conversation, human_conversation, session_direction`

- `conversation_type`：對話管道類型（取代舊版 `sessionable_type`）
- `chatbot_conversation` / `human_conversation`：分別為機器人 / 真人客服對話全文，兩者依序串接（換行分隔）成統一問題列表的 `content`（見 `config/ai_judge/source_mapping.json` 的 `conversations.merge_fields`）
- `msg_handler`：`KKDAY` / supplier

## 對應規格
- SQL 出處：H2 內容治理規劃 Doc（`1MN_aLEzpIlsOM1G9IoGO1sXZ_eUPjXLldMicfqyrxmA`）
- 規格：repo `docs/PLAN-V3-售前售後進線.md` 附錄、Confluence「① 如何整合（V3）」

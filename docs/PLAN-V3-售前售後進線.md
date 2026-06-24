# AI 法官 V3 規劃 — 以「售前售後進線」為第一串接管道

> 基準：Confluence「AI 法官」五層架構（page 2105442335）+ Phase 1 SA/SD（2108063814）+ 感知收集多管道（2109243415）。
> 強化方向（本次新指令）：**第一個串接管道改為「售前售後進線 SQL 拉取客服信息」**，並為後期其他管道預留劃分。
> 資料源權威：H2 內容治理規劃 Doc（`1MN_aLEzpIlsOM1G9IoGO1sXZ_eUPjXLldMicfqyrxmA`）內已驗證 SQL。
> 日期均為 ⚠️ 預估，受外部 gate 約束（OpenAI key 6/25、工單 API 6/30、order/DB 權限）。

## 0. 與既有規劃的差異（必讀）

| 項 | 既有 repo（spec 01）| Confluence Phase 1 | **本 V3 新方向** |
|---|---|---|---|
| 第一管道 | 商品差評（`ReviewAdapter`）| FreshDesk 工單 | **售前售後進線**＝FreshDesk 工單 **＋** 訂單訊息/chatbot |
| 取數 | Review API | 批次 SQL → 6/30 API | **BigQuery 批次 SQL（兩條已驗證）** 起手，6/30 API 接替 |
| 評論定位 | 主輸入 | 未列 | **降為旁路印證訊號**（交叉加權，非主管道）|

決策理由：售前售後進線是**真實負面訊號 + 已含客服標準答案（ground truth）**的管道，SQL 現成可拉，最快打通「intake → 判定 → action 診斷 → dashboard」整鏈；評論缺客服對話，僅作交叉印證。

## 1. 管道劃分（為後期擴展定架構）

統一抽象 `IntakeAdapter.fetch() -> NormalizedTicket[]`，下游 L2–L5 不感知來源。新增管道只加 adapter。

| 管道 | 階段 | 來源 / 資料表 | 狀態 | 優先序 |
|---|---|---|---|---|
| **B-售前** | 售前 | FreshDesk 工單 `dw_third_party.freshdesk_tickets`（`prod inquiry` / `Customer Complain Record`）| ✅ SQL 已驗證 | **P1 首發** |
| **B-售後** | 售後 | 訂單訊息 + chatbot `dw_kkdb.message` / `message_session` / `dw_kkdb_chatbot.chatbot_messages` | ✅ SQL 已驗證 | **P1 首發** |
| A-行中 | 行中 | 行中關懷 + Feedback（新 UI/推播）| ❌ 待建 | P2 |
| C-供應商 | — | 供應商申訴（平台客服優化 · PM Wei · H1）| ❌ 待建 | P3 |
| 旁路-評論 | 售後 | 商品評論 AI Summary | ✅ 有 | P2 印證 |
| 旁路-Mixpanel/NPS | 全程 | 行為事件 / NPS | — | P2 印證 |

## 2. 四問完整規格

### ① 如何整合（L1 接入）— 整合售前售後進線 SQL 拉取客服信息

**規格**
- 兩個 SQL adapter（落點 `backend/app/judge/ingest/`）：
  - `FreshdeskTicketAdapter`（售前）— filter `ticket_type IN ('prod inquiry','Customer Complain Record')`，**僅取 Tour 類目**。
  - `OrderMessageAdapter`（售後）— 聚合 SQL（見 §附錄 A），含角色改寫 `K=KKday客服 / M=user / S=供應商`、`msg_oid BETWEEN start/end` 包夾、`order_lst → prod_oid`、`tableau_ec_index → prod_name/order_profit`（排除 B2D）、`supplier → msg_handler`。
- 取數方式：**現況 BigQuery 批次 SQL（T+1）**做離線原型 + golden 貼標；正式工單 6/30 改版後走 API（即時）。
- `NormalizedTicket` schema 擴充（`backend/app/core/schema.py`）：

  | 欄位 | 售前（工單）| 售後（訂單訊息）|
  |---|---|---|
  | `ticket_id`（冪等鍵）| freshdesk ticket id | `session_oid` |
  | `source` | `freshdesk_ticket` | `order_message` / `chatbot` |
  | `prod_oid` | 工單關聯 | `order_lst.prod_oid` |
  | `order_oid` / `supplier_oid` | — / — | ✅ / ✅ |
  | `cs_conversation` | 工單對話 | `aggregated_messages`（多輪、含角色）|
  | `msg_handler` | — | `KKDAY` / supplier |
  | `created_at` | `created_at` | `session_create_date` |

- 冪等：售前以 `ticket_id`、售後以 `session_oid` 去重，重跑覆蓋不產重複 Finding。
- 失敗策略：單筆 parse 失敗 → dead-letter（`findings/_deadletter.jsonl`），不中斷批次。
- **AI 精準分類貼標**（取代過往人工/規則貼標）= 自由文本 → Rule ID，本階段**第一新建項**。

**交付**：⚠️ 2–3d｜**依賴**：BigQuery 讀取權限（DB 權限 Gary 申請中）。

### ② 如何建置 dashboard（L5）— 從「問題來源 × 問題判斷」雙軸規劃

**規格**（兩出口 + ECharts 多元，唯讀聚合）

雙軸維度設計：
- **問題來源軸**：管道（A/B-售前/B-售後/C）× 系統來源（FreshDesk / 訂單訊息 / chatbot）× `msg_handler`（KKday 客服 vs 供應商處理）。
- **問題判斷軸**：4 判定層（1 / 2 / 3A / 3B）× 8 面向 × Rule ID × 嚴重度 × 信心度。

ECharts 7 圖（Phase 1 至少 4–5 種）：

| 圖表 | 類型 | 回答 |
|---|---|---|
| 判定層分布 | pie/rose | 內容問題(1/2/3A) vs 服務(3B) 佔比 |
| Rule ID Top-N | bar 橫 | 哪些規則最常被違反 → 優先補強 |
| 風險等級 | bar/stacked | High 風險量 → 處理優先序 |
| 進線→判定→action 趨勢 | line 多軸 | 量隨時間（含售前/售後分線）|
| 商品/供應商熱點 | scatter/heatmap | 高問題商品/供應商 → 盯盤對象 |
| action 待辦看板 | KPI cards | 要求修正/計點/通報/修法 各待辦數 |
| 處理漏斗 | funnel | 工單→判定→action→結案 轉化/卡點 |

兩出口（落點 `frontend/apps/console/`）：
- **出口B｜RD/品控**（進門第一眼）：dimension × verdict **熱力矩陣** + KPI 列 + 下鑽 + **規則缺口面板**（高頻 unclear/missing 但無 Rule → 標紅 CTA）+ **來源面板**（售前 vs 售後 vs 管道分布）。
- **出口A｜PM/AM 單品頁**：選商品 → Finding 依 `suspected_field` 分組 + 卡片（客戶原話 / 頁面 evidence / **客服標準答案綠底可複製** / recommended_action）+ 狀態（確認/忽略/已修）。
- 後端 API：`GET /api/findings`、`GET /api/findings/aggregate`、`PATCH /api/findings/{id}/status`。MVP 批次預聚合 `aggregate.json` 前端直讀。

**交付**：⚠️ 3d｜**依賴**：④ 產出 Finding store。

### ③ 如何後續調用其他資料（L0 + function-calling tools）

**規格**（每資料源包成 OpenAI SDK tool，LLM 自主呼叫 — Gary 構想；落點 `backend/app/judge/datasource/`）

| tool | 用途 | 來源 | 狀態 |
|---|---|---|---|
| `fetch_product` + `extract_fields` | 商品 9 欄原文（判內容是否合規）| api-b2c CDN | ✅ 可做 |
| `fetch_order` | 訂單/履約事實（第 3A 層加權）| order API / `order_lst` | ⚠️ 待權限 |
| `fetch_reviews` | 評論交叉印證（旁路）| Review API | ✅ 已驗證 |
| BigQuery 關聯 | `order_lst`(prod_oid) / `tableau_ec_index`(商品名·利潤·BD tag) / `supplier`(msg_handler) | DAP | ✅ SQL 內已串 |

**聯合判定**（多管道 → 同一標的）三步：①關聯（綁商品/訂單/供應商）②去重（同問題多管道只裁一次）③加權（多管道印證 → 提信心/嚴重度）。Phase 1 單管道先不做跨管道 merge，但 schema 預留 `signal_sources[]`。

**交付**：商品 tool ⚠️ 2d（與①並行）；order/聯合判定列 P2。

### ④ 如何產出可執行 action 診斷（L2–L4，核心）

**規格**（文本→Rule ID 分類 → 兩階段 + 雙意見 + 純程式仲裁）

1. **L2 classify**（只看進線文本/客服對話）：→ 8 dimension + `problem_summary` + `suspected_field` + `hit_rule_id[]` + `tentative_verdict` + confidence。非內容 → `escalate_ops`。
2. **fetch_product + extract_fields**：取 `suspected_field` 原文。
3. **L3 adequacy**（第二意見，只看商品原文、不採信抱怨）：→ `adequate/unclear/missing/contradictory/field_empty` + evidence。**客服需搬政策原文才解釋 → 強烈傾向 unclear/missing**。
4. **L3 arbiter**（純程式仲裁表）：classify × adequacy → **verdict 五分類 / 4 判定層** + 信心度（內容證據凌駕客訴語氣）。
5. **L4 diagnose**（純程式）：→ `recommended_action` + `action_target`（SCM2.0 / Be2 / PM / 客服）+ `action_detail`（**客服對話當 ground truth**）+ `writer_handoff`（content_missing 一律 False 防幻覺）。

4 判定層 → action 對照：

| 判定層 | 條件 | action | action_target |
|---|---|---|---|
| 第1層 | Rule 已定義·內容沒寫對 | 要求供應商修正 | SCM2.0 違規中心 |
| 第2層 | 框架未定義·達閾值 | 回饋修法 | PM 提案 Rule ID |
| 第3A層 | 內容承諾履約不符 | 計點違規 + 改善 | SCM2.0 + M5 計點 |
| 第3B層 | 非內容服務履約 | 感知通報（僅通報）| 客服/營運協作 |

診斷輸出 schema（單筆）：`{ diagnosis_id, source, ticket_id, 標的{product_id,pkg_oid,supplier_oid}, hit_rule_id[], 判定層, 風險等級, 嚴重度, 信心度, 建議action, action_target, ruling_reason(繁中), status }`。

**信心度路由**：高 → 可自動 action；低 → 轉人工 + 沉澱 golden。MVP 全進 `status=new` 待人工，蒐 golden 後再調門檻（高信心誤判代價最高，保守）。

**驗收**（Promptfoo + golden）：verdict 準確率、`suspected_field` 命中率、`customer_misread` 降級精確率；**先鎖「集合/費用」2 dimension** 打穿門檻再開放 8。第 2/3A/3B 層 Phase 1 先**標記分類**不自動執行。

**交付**：⚠️ 4d｜**依賴**：OpenAI key 6/25（之前用 stub 跑通流程）。

## 3. 里程碑（關鍵路徑）

| 期間 | 階段 | 方面 | gate |
|---|---|---|---|
| W1 前半 | M1 接入（並行）| ① 售前+售後 SQL adapter + ③ 商品 tool | BigQuery 權限 |
| W1 後半–W2 | M2 判決核心 | ④ 文本→Rule ID 分類 + 兩階段判定 | ⚠️ OpenAI key 6/25 |
| W2 後半 | M3 可視化 | ② ECharts 兩出口 | 需 M2 Finding |
| W3 | M4 閉環 | golden/eval + 信心度 calibration + 規則缺口回灌 | 需人工已裁決 golden |
| W4+ | P2 擴展 | A 行中關懷 + C 供應商 + 評論/Mixpanel 旁路 + 聯合判定 | 管道權限/UI |

## 4. 風險與依賴

| 風險 | 說明 / 緩解 |
|---|---|
| 售前管道實際未開 | `prod inquiry` 進線 0.01% 為假數字（CS 量能未開）→ Phase 1 以**售後訂單訊息**為主力樣本，售前先小樣本 |
| 文本→Rule ID 精度 | 自由文本分類精度未知 → 先小樣本人工標註做基準（同審品 golden）|
| 第3A 需履約事實 | order/履約資料源 repo 無接口 → 第3A Phase 1 先標記不自動計點 |
| 高信心誤判 | = 錯誤自動執法，代價高於審品 → 門檻保守，MVP 全待人工 |
| 範圍爆炸 | 8 面向 × 58 欄位不一次做 → 嚴格分期，第1層 + 售後管道起手 |

## 附錄 A · 售前售後進線 SQL（已驗證，來自 H2 規劃 Doc）

**售前（FreshDesk 工單）**
```sql
SELECT * FROM `kkday-data-dap.dw_third_party.freshdesk_tickets`
WHERE created_at >= '2025-06-01'
  AND ticket_type IN ('prod inquiry', 'Customer Complain Record');
```

**售後（訂單訊息 + chatbot 聚合）** — 角色改寫 K=客服/M=user/S=供應商，關聯 prod_oid / 商品名 / 利潤 / msg_handler：
```sql
WITH chatbot_agg AS (
  SELECT session_id,
         STRING_AGG(CONCAT(message_sender, ': ', COALESCE(message_content, '')), '\n'
                    ORDER BY create_date ASC) AS chatbot_conversation
  FROM `kkday-data-dap.dw_kkdb_chatbot.chatbot_messages`
  GROUP BY session_id
  HAVING COUNTIF(message_sender = 'user') >= 1
),
order_msg_agg AS (
  SELECT ms.session_oid, ms.session_direction, ms.supplier_oid,
         STRING_AGG(CONCAT(
           CASE m.send_type WHEN 'K' THEN 'KKday 客服' WHEN 'M' THEN 'user'
                            WHEN 'S' THEN '供應商' ELSE m.send_type END,
           ': ', COALESCE(m.msg_content, '')), '\n' ORDER BY m.create_date ASC) AS order_conversation
  FROM `kkday-data-dap.dw_kkdb.message_session` AS ms
  INNER JOIN `kkday-data-dap.dw_kkdb.message` AS m
    ON m.order_oid = ms.order_oid AND m.msg_oid BETWEEN ms.start_msg_oid AND ms.end_msg_oid
  GROUP BY ms.session_oid, ms.session_direction, ms.supplier_oid
)
SELECT s.session_oid, s.zendesk_ticket_id, s.create_date AS session_create_date,
       c.order_oid, c.order_mid, sm.sessionable_type, sm.sessionable_id, ol.prod_oid,
       CASE WHEN sm.sessionable_type = 'order_message' THEN oma.session_direction END AS session_direction,
       CASE WHEN sm.sessionable_type = 'order_message' THEN oma.supplier_oid END AS supplier_oid,
       CASE WHEN sm.sessionable_type = 'chatbot' THEN 'KKDAY' ELSE sup.msg_handler END AS msg_handler,
       CASE WHEN sm.sessionable_type = 'chatbot' THEN cb.chatbot_conversation
            WHEN sm.sessionable_type = 'order_message' THEN oma.order_conversation END AS aggregated_messages,
       te.prod_bd_tag_note, te.prod_name_zh_tw, te.order_profit
FROM `kkday-data-dap.dw_kkdb_imassage.session` AS s
INNER JOIN `kkday-data-dap.dw_kkdb_imassage.channel` AS c ON s.channel_oid = c.channel_oid
INNER JOIN `kkday-data-dap.dw_kkdb_imassage.session_mapping` AS sm ON s.session_oid = sm.session_oid
LEFT JOIN (
  SELECT order_oid, prod_oid FROM `kkday-data-dap.dw_kkdb.order_lst`
  QUALIFY ROW_NUMBER() OVER(PARTITION BY order_oid ORDER BY prod_oid DESC) = 1
) AS ol ON c.order_oid = ol.order_oid
LEFT JOIN (
  SELECT order_oid, prod_bd_tag_note, prod_name_zh_tw, order_profit
  FROM `kkday-data-dap.dm_tableau.tableau_ec_index`
  WHERE order_create_source_code != 'B2D'
  QUALIFY ROW_NUMBER() OVER(PARTITION BY order_oid ORDER BY order_profit DESC) = 1
) AS te ON c.order_oid = te.order_oid
LEFT JOIN chatbot_agg cb ON sm.sessionable_id = cb.session_id
LEFT JOIN order_msg_agg oma ON SAFE_CAST(sm.sessionable_id AS INT64) = oma.session_oid
LEFT JOIN `kkday-data-dap.dw_kkdb.supplier` AS sup ON oma.supplier_oid = sup.supplier_oid
WHERE DATE(s.create_date) BETWEEN '2026-04-01' AND '2026-04-02'  -- 依需求調整區間
  AND ((sm.sessionable_type = 'chatbot' AND cb.chatbot_conversation IS NOT NULL)
    OR (sm.sessionable_type = 'order_message' AND oma.order_conversation IS NOT NULL))
ORDER BY s.create_date DESC;
```

## 附錄 B · 來源索引
- AI 法官五層架構：Confluence 2105442335
- Phase 1 SA/SD 四問：Confluence 2108063814
- 感知收集多管道：Confluence 2109243415
- H2 內容治理 + SQL：Google Doc 1MN_aLEzpIlsOM1G9IoGO1sXZ_eUPjXLldMicfqyrxmA
- PRD/法典/四支柱：Google Doc 1f5K5nbimFDliGMOn6J3IMtjtilKtn4CcEf30Kd00eiw
- 法典 SSOT（8 面向 58 欄位）：Sheet 1-nGP5uvCXcn4TJ9iuLYkfbMZC6_xZE7vPBbNEXTHY6s
- 本地架構圖：`/Users/alvin/Kkday/work/AI質檢/KKday_Tour_Architecture_Diagram_v3_1.html`
- repo 既有規格：`docs/specs/01~04`、`docs/ARCHITECTURE.md`、`docs/DELIVERY-PLAN.md`

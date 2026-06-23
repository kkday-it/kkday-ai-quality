# kkday-ai-product-quality — 交付計劃（按 Aaron 四問歸類）

> 計劃以 folder 2117435397 的「四問」為主軸，每方面含**規格**與**交付時間**。
> 基準日 2026-06-22。日期為 ⚠️ 預估，受外部 gate（OpenAI key 6/25、工單 API 6/30、order/DB 權限申請中）約束。
> 對應分層：①整合=L1 · ②dashboard=L5 · ③調用資料=L0 · ④action 診斷=L2–L4。

## 總時間軸（關鍵路徑）

| 期間 | 階段 | 方面 | gate |
|---|---|---|---|
| 6/23–6/24 | M1 資料層（並行）| **①整合** + **③商品調用** | 無（評論/商品已驗證可拉）|
| 6/25–6/30 | M2 判決核心 | **④action 診斷** | ⚠️ OpenAI key 6/25 生效 |
| 7/1–7/3 | M3 可視化 | **②dashboard** | 需 M2 產出 Finding |
| 7/4–7/7 | M4 閉環 | golden/eval + 信心度 calibration | 需一批人工已裁決 golden |
| 7/8+ | P2 擴展 | **③order/工單調用** + 多管道 | ⚠️ order 權限 + 工單 API 6/30 |

人天 13d（①③並行壓縮日曆）；MVP（①③④②）約 6/23–7/3。

---

## ① 如何整合（L1 接入）

**規格**
- adapter 模式：`TicketSourceAdapter.fetch()` → 正規化成 `NormalizedTicket`（schema 見 `packages/shared`）。
- MVP 單管道：**評論差評**（`ReviewAdapter`，`sort=RATING_ASC`，已驗證 150665 可拉）。
- 冪等：以 `ticketId`（review id / thread ts）去重，重跑不產重複 Finding。
- 失敗策略：單筆失敗不中斷批次，記 dead-letter。
- 來源可換：新增 adapter（工單/訂單訊息）不動 L2–L5。

**交付**：6/23–6/24（2d）｜產出：評論 → `NormalizedTicket[]`，含 150665 真實 10 筆差評。
**依賴**：無（評論 API 已驗證）。

---

## ② 如何建置 dashboard（L5）

**規格**（兩出口，唯讀聚合，Vue3 + vue-echarts）
- **出口B｜RD/品控**：dimension × verdict **熱力矩陣** + KPI 列 + 下鑽清單 + **規則缺口面板**（高頻 content_unclear/missing 但無對應規則 → 標紅 CTA）。
- **出口A｜PM/AM 單品頁**：選商品 → Finding 依 `suspectedField` 分組 + 卡片（客戶原話 / 頁面 evidence / 客服標準答案 / recommendedAction）+ 狀態（確認/忽略/已修）。
- MVP：批次預聚合 `aggregate.json`，前端直讀，不做即時查詢。
- 不引重 BI，熱力矩陣/sparkline 用 ECharts 自繪。

**交付**：7/1–7/3（3d）｜產出：兩分頁可讀 Finding 並正確聚合。
**依賴**：M2 產出的 Finding store。

---

## ③ 如何後續調用其他資料（L0 + function-calling tools）

**規格**（每資料源包成 AI SDK tool，LLM 自主呼叫 — Gary 構想）
| tool | 規格 | 狀態 |
|---|---|---|
| `fetchProduct({prodId})` + `extractFields` | api-b2c CDN + 固定 header → 9 邏輯欄位原文 | ✅ 可做（6/23–24）|
| `fetchReviews({prodId,sort,page})` | Review API，差評優先 | ✅ 已驗證 |
| `fetchOrder({orderId})` | 該客人訂單詳情（履約事實，第3A層需要）| ⚠️ 待 order 權限（Gary 申請中）|
| `fetchTicket({ticketId})` | 工單 + 客服對話（ground truth）| ⚠️ 待工單 API 6/30 |

- agent prompt：「可用以上 tools 補足判斷所需證據，齊備後輸出 Finding。」
- 一次性拉取上限、分頁策略：評論單品已驗證可翻頁；order/工單拉取量待確認。

**交付**：商品 tool 6/23–6/24（2d，與①並行）；order/工單 tool 列 **P2（7/8+）**。
**依賴**：商品無依賴；order 權限 + 工單 API。

---

## ④ 如何產出有用可執行的 action 診斷（L2–L4，核心）

**規格**（兩階段 + 雙意見 + 純程式仲裁）
- **L2 classify**（只看客訴）：→ 8 dimension + problemSummary + 疑似欄位 + 初判。
- **L3 adequacy**（第二意見，只看商品原文、不採信抱怨）：→ adequate/unclear/missing/contradictory + evidence。
- **L3 arbiter**（純程式）：classify × adequacy → **verdict 五分類** + 信心度。
- **L4 diagnose**：verdict → recommendedAction + actionDetail（**客服對話當 ground truth**）+ writerHandoff（防幻覺：content_missing 一律 false）。
- **信心度路由**：低信心不自動 action，轉人工 + 沉澱 golden。
- 輸出契約：`TicketFinding`（schema 已定義於 `packages/shared`）。

**交付**：6/25–6/30（4d）｜產出：150665 差評 → Finding[]（含纜車案例判 content_unclear）。
**依賴**：⚠️ **OpenAI key 6/25**（之前用 stub 跑通流程，key 到換真 LLM）。

---

## 驗收（M4 閉環）
- 以 `fixtures/product_150665.json` golden + Promptfoo 算 verdict 準確率、`customer_misread` 降級精確率。
- 先鎖「集合/費用」2 dimension 打穿準度門檻，再開放 8 個（準度優先於覆蓋）。
- 信心度門檻 calibration 需一批人工已裁決爭議。

## 風險與前置（沿用前期識別）
- OpenAI key 6/25 · 工單 API 6/30 · order/DB 權限（Gary）· 評論 production 走內網 Review Service 避 datadome · 信心度門檻待 calibration。

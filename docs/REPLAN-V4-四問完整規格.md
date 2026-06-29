# AI 法官 — 四問完整重新規劃方案 v4

> 重新規劃緣由：對齊《KKday Tour 內容治理系統 架構圖 v3》（`/Users/alvin/Kkday/work/AI質檢/KKday_Tour_Architecture_Diagram_v3_1.html`），
> 並修正先前規劃的理解偏差（Q3 版本綁定、Q2 分類細分、verdict 五→六分類殘留）。
> 基準文件：`docs/specs/01~06`、`docs/PLAN-V3-售前售後進線.md`、`docs/UPSTREAM-REFS.md`、Confluence 整合版 V2（2125561898）+ 子1–子8、memory（PMDL 對照 / BQ 成本 / 進線 OID / DB 10 表）。
> 整理：2026-06-24。日期均為 ⚠️ 預估，受外部 gate（OpenAI key、工單 API 6/30、order/BQ 權限）約束。

## 0. 與架構圖 v3 的五層對映（系統定位）

架構圖 v3 是完整「Tour 內容治理系統」（三支柱）；**AI 法官＝其中事後裁決閉環**＝感知層(管道B) + 整合層 + 自動判定引擎 + 執行層（輸出），共用核心大腦（治理規則庫/法典）。

| 架構圖 v3 層 | AI 法官對應 | 四問 | repo 模組 |
|---|---|---|---|
| 核心大腦·治理規則庫（8面向×60欄·Rule ID·風險·Phase·Canon） | 法典 codex（沿用，SSOT＝Google Sheets） | 共用地基 | `judge/codex.py` + `field_codex.json` + `judge_rules.json` |
| 感知層·管道 B（客人主動進線：工單/訂單訊息/chatbot/評論） | L1 intake | **Q1** | `judge/ingest/` |
| 整合層（以 Rule ID 為共同語言，彙整 + 跨管道合併 + 交叉比對） | L1.5 整合 + L0 資料調用 | **Q1/Q3** | `judge/ingest/` + `judge/datasource/` |
| 自動判定引擎（4 判定層 1/2/3A/3B → 對應行動） | L2 classify · L3 adequacy+arbiter · L4 diagnose | **Q4** | `judge/{classify,adequacy,arbiter,diagnose}.py` |
| 執行層（SCM2.0/Be2/PM/客服）+ Dashboard | L5 dashboard + action 輸出 | **Q2/Q4** | `frontend/` + `api/` |
| 閉環（Feedback→修法→規則庫進化） | 規則缺口面板 + golden 回灌 | Q2/Q4 | dashboard 規則缺口 + golden |

> 邊界（沿用架構圖）：AI 法官是**規則制定者與實驗室 + 用戶感知捕捉者 + 問題判定者**；不介入 SCM2.0/Be2 的 UI/UX，不執行客服補救 SOP。只取它需要的資料、只輸出判定結果。

## 1. 梳理結論：既有文件盤點 + 過時/衝突項

| 文件 | 貢獻 | 過時/衝突（本次修正） |
|---|---|---|
| `specs/01-integration` | ReviewAdapter / NormalizedTicket / 冪等 | 第一管道已從「商品差評」改「售前售後進線」（見 PLAN-V3）；評論降旁路 |
| `specs/02-dashboard` | 兩出口 + 熱力矩陣 + API | 「出口A/B」字眼已棄用→RD/品控·PM/AM；缺「症狀→歸因」分類框架 |
| `specs/03-datasource` | fetch_product/extract_fields/tools | **「撈最新」需改為「版本綁定」**（Q3 核心修正）；`verify=False` 待修；extract 欄位映射用舊 api-b2c path，已改 BQ PMDL |
| `specs/04-action-diagnosis` | L2-L4 判決鏈 + 仲裁表 | **verdict 寫「五分類」→ 應六分類**（schema.py 已含 contract_breach）|
| `specs/05-codex`、`06-v2` | 法典 60 欄 + v2 體系 | 與 Confluence 整合版 V2 一致 |
| `PLAN-V3-售前售後進線` | 售前售後 SQL（已驗證）+ 管道劃分 + 四問規格 | 現行主基準；本 v4 在其上補 Q2 分類框架 + Q3 版本綁定 |
| memory PMDL 對照 | 8 面向→PMDL 路徑 + 進線 OID 關聯 | — |
| memory BQ 成本 | 字面剪枝唯一解（cluster pruning） | — |
| memory 進線 OID 覆蓋 | order_message 帶 order+supplier 無 prod；chatbot 帶 prod 無 order/pkg | 影響 Q3 版本綁定可行性 |
| memory DB 10 表 | 本地 SQLite schema | packages=0/pkg_quality=0（缺方案層資料）|
| code `schema.py` | NormalizedTicket / TicketFinding / verdict 六分類 ✅ | **`LogicalField` 只 7 個**（缺 prod_fee/meetup/redeem）需補齊到 9 |
| code `product_refresh.py` | 方案①字面剪枝即時撈**最新** | **需擴版本綁定模式**（見 Q3）|

## 2. 四問完整規格

### Q1 · 如何整合（L1 intake）

**現況**：售前（FreshDesk 工單）+ 售後（訂單訊息/chatbot）SQL 已驗證、adapter 已落地（`ingest/presale_postsale.py`）。

**規格**
- 統一介面 `IntakeAdapter.fetch() -> list[NormalizedTicket]`，下游 L2–L5 不感知來源；新管道只加 adapter。
- **管道優先序**：

  | 管道 | 來源 | OID 覆蓋（真實特性）| 狀態 | 優先 |
  |---|---|---|---|---|
  | B-售後·訂單訊息 | `dw_kkdb.message(_session)` | order+supplier，**無 prod**（pkg 多為'0'）| ✅ 已驗證 | P1 主力 9.41% |
  | B-售後·chatbot | `dw_kkdb_chatbot.*` | prod，**無 order/pkg/supplier** | ✅ 已驗證 | P1 |
  | B-售前·工單 | `dw_third_party.freshdesk_tickets` | 工單關聯 | ✅ 已驗證（0.01% 假數字，CS 未開）| P1 旁路 |
  | **評論** | 商品評論 AI Summary / Review API | prod（無 order/客服對話）| ✅ API 可拉 | **P2 旁路印證** |
  | **工單（新系統）** | 6/30 改版後 API | 待確認 | ⚠️ 6/30 | P2 |
  | A-行中 / C-供應商 | 待建 UI / SCM2.0 申訴 | — | ❌ | P3 |

- **待補 adapter 規格**：
  - **評論 adapter**（P2）：`fetch_reviews(prod_oid, sort=RATING_ASC)` → NormalizedTicket（`source=review`，`cs_conversation=[]`，rating 為嚴重度訊號）。**定位＝旁路印證**：無客服對話＝無 ground truth，不可單獨產 contract_breach，僅對同商品同 Rule 的 B 管道 finding **加權信心/嚴重度**。上限 150（proxy）→ 全量走內網。
  - **工單 adapter**（P2，6/30）：新系統 API 接替批次 SQL；需與工單 PM 確認**是否回傳結案訊號**（架構圖標「待確認」）。
- **整合層（架構圖要求，本次補規格）**：以 Rule ID 為共同語言，每筆 finding 標 `prod_oid / pkg_oid / supplier_oid / order_oid / hit_rule_id / source_channel`；**跨管道合併三步**：①關聯（綁同商品/訂單/供應商）②去重（同問題只裁一次）③加權（多管道印證提信心）。Phase 1 單管道先不 merge，schema 預留 `signal_sources[]`。
- **AI 精準分類貼標**（取代人工/規則貼標，本階段第一新建項）：自由文本 →（症狀 tag1/2/3 + Rule ID + 8 面向），對接 Q2 分類框架。
- 冪等：售前 `ticket_id` / 售後 `session_oid` 去重；parse 失敗 → dead-letter 不中斷。

**依賴**：BQ 讀取權限（Gary）；工單新 API（6/30）。**驗收**：售後樣本 → NormalizedTicket，重跑不重複；評論加權正確。

### Q2 · 如何建置 Dashboard（L5）— 含完整分類框架

**核心：在 KKday 既有「進訊兩層分類 + 旅行階段」(類 Image#22) 之上，疊加 AI 法官「歸因細分」。**

#### 2.1 分類框架（四軸）
- **軸一 症狀軸（沿用 KKday 進訊）**：tag1 / tag2 / tag3 + 旅行階段（PRE/DURING/POST）。＝客人抱怨什麼（入口漏斗）。
- **軸二 歸因軸（核心新建·6 大責任域）**：

  | L1 歸因域 | L2 子類 | verdict | 判定層 | 責任方 |
  |---|---|---|---|---|
  | ① 商品內容問題 | 8 內容面向×欄位×Rule | content_missing / content_unclear / real_config_issue | 第1/2層 | 供應商內容 + PM 修法 |
  | ② 供應商履約問題 | 現場人員/集合/語言/成團/設備/安全 | contract_breach | 第3A | 供應商計點(SCM2.0/ERC) |
  | ③ 訂單與交易問題 | 確認/修改/取消/退款/付款/憑證 | escalate_ops（少數 real_config_issue）| 第3B / OOS | 平台/系統/客服 |
  | ④ 平台與系統問題 | 頁面bug/搜尋/流程/通知 | escalate_ops | 第3B | RD/PM |
  | ⑤ 客服與營運問題 | 回應SLA/態度/補救SOP | escalate_ops | 第3B | 客服 SOP |
  | ⑥ 客人理解/期待落差 | 內容清楚但誤解/期待過高 | customer_misread | — | 無責罰（UX 洞察）|

- **軸三 判定軸**：4 判定層 × 8 面向 × Rule ID × 嚴重度(P0-P3) × 信心度。
- **軸四 行動軸**：recommended_action × owner_role × exec_platform。
- **設計原則（落地自成熟 RCA）**：症狀≠根因≠責任三層分離；階層因果樹可下鑽聚合；每條鏈收斂到「可改控制點 + owner」。
- **裂解示例**：症狀「現場工作人員>司機/導遊問題」→ 三種根因（內容寫了沒做到＝②contract_breach / 內容沒寫＝①content_missing / 內容清楚客人沒看＝⑥customer_misread）→ 三種 action。

#### 2.2 Dashboard 呈現（三層下鑽）
1. **上層漏斗**（familiar）：症狀 tag1/tag2 長條 + 旅行階段甜甜圈（同 Image#22）。
2. **歸因下鑽**：點症狀 → 6 歸因域分布（責任方）→ 8 面向×verdict 熱力矩陣 → Rule ID Top-N。
3. **責任歸屬卡**：本期 該供應商修內容% / 供應商計點% / 平台系統% / 客人誤解%（可行動 vs 不可行動）。
- **圖表清單**（ECharts，Phase 1 至少 4-5）：症狀兩層長條、旅行階段環、歸因域 pie、面向×verdict 熱力、Rule ID Top-N bar、判定層分布、商品/供應商熱點 scatter、action 待辦 KPI、處理漏斗。
- **規則缺口面板**：高頻 content_missing 但 `has_rule=false` → 標紅「建議新增 Rule」CTA（閉環北極星）。
- **兩視角**：RD/品控分析（聚合熱力+缺口）；PM/AM 單品（FindingCard 依欄位分組，**OID 用 v-if 條件渲染**因進線 OID 覆蓋不齊）。
- **API**：`GET /api/findings`、`GET /api/findings/aggregate`、`PATCH /api/findings/{id}/status`；MVP 批次預聚合 `aggregate.json`。

**依賴**：Q4 產出 Finding store。**驗收**：兩視角正確聚合；三態（loading/empty/error）；status 寫回生效。

### Q3 · 如何調用其他資料（L0）— 版本綁定（核心修正）

> **最大修正**：先前是「撈當下最新內容」(`product_refresh.py` 方案①)，本次要「**綁定當前訂單對應的商品&方案對應版本**」。兩者是不同資料時態，用途不同，須並存。

#### 3.1 兩種資料時態（必須分清）

| 時態 | 用途 | 取哪個版本 | 現況 |
|---|---|---|---|
| **下單/出遊版本快照** | **爭議裁決**：判斷客人「當時看到的內容」是否誤導 → contract_breach / customer_misread 的事實基礎 | 訂單成立時 prod/pkg 的內容版本 | ❓ **待查資料源**（核心 gap）|
| **當下最新** | **內容治理盤點**：判斷「現在內容」是否仍有問題（避免已修誤報）| 最新 product_summary | ✅ 已實作（方案①字面剪枝即時撈）|

→ **裁決一筆爭議要同時看兩者**：用「下單版本」判客人是否被誤導；用「最新版」判現在是否還要供應商改（已修則只記錄不重罰）。

#### 3.2 版本綁定可行性探查（P1 必做調研）
- ❓ `dw_kkdb_product.product_summary` 是否有**版本/歷史表**或 `version/updated_at` 可回溯下單時內容？（目前 memory 僅見 current-state，無版本欄）
- ❓ 訂單成立時是否有**內容快照**（order snapshot）？若有，直接綁 order→snapshot 最準。
- **退路方案**（若無版本資料）：
  - A. **裁決即快照**：判決當下把 prod/pkg 內容存進本地 `judgments` 一併留存（至少保住「判決時點」內容，供事後復查）。
  - B. 以 `order_lst.create_date` 對齊**最近一版**（若有 history 表按時間取）。
  - C. 明確標註「以最新版裁決」並在 dashboard 標記時態風險（最低標）。

#### 3.3 訂單 → 商品&方案 綁定
- 進線 OID 覆蓋（真實特性）：order_message 帶 order+supplier 無 prod；chatbot 帶 prod 無 order/pkg。
- 綁定鏈：`order_oid → order_lst.prod_oid + prod_level2_oid(=pkg_oid)`；缺 prod 時 chatbot 直帶；`COALESCE` 合併。
- **方案層 (pkg) 是缺口**：進線 CSV 無 pkg_oid（packages=0），需 `order_lst.prod_level2_oid` 補 + 比對 order_oid+prod_oid 防綁錯。

#### 3.4 取數機制（BQ 硬約束）
- DAP 只准純 SELECT；降成本唯一解＝**字面剪枝**（cluster pruning，僅認 `prod_oid IN (字面)`，子查詢/JOIN 不剪枝）。
- 編排（已實作 `product_refresh.py`）：step1 算當批 prod_oid → step2 字面注入 `product_content_by_oids.sql` → step3 fixture/live upsert 本地 DB。
- **生產正解**：請 DAP 開 scripting/CREATE TABLE/排程 → 物化商品內容（含版本）一次、進線 join。
- extract_fields 欄位映射改用 **PMDL 路徑**（見 memory pmdl-dimension-mapping，非舊 api-b2c path）；商品 JSON ~10萬 chars → extract 後才餵 LLM。

**依賴**：BQ 權限（Gary）+ **版本資料源確認（新增 gate）**。**驗收**：給定 order_oid → 取到對應 prod/pkg（下單版本快照 or 標註退路），9 邏輯欄位齊。

### Q4 · 如何產出可執行 action 診斷（L2–L4，核心）

**判決鏈**：`NormalizedTicket → L2 classify(只看客訴) → 取 suspected_field 原文(Q3) → L3 adequacy(只看原文·第二意見) → arbiter(純程式仲裁) → L4 diagnose → TicketFinding`。

**規格**
- **L2 classify**（LLM#1）：客訴文本 → 1..N {症狀tag + dimension + suspected_field + hit_rule_id[] + 歸因域(L1) + tentative_verdict + confidence + is_primary}。非內容→escalate_ops。
- **L3 adequacy**（LLM#2）：只看商品原文、不採信抱怨 → {adequate/unclear/missing/contradictory/field_empty} + evidence。客服需搬政策原文才解釋 → 傾向 unclear/missing。
- **L3 arbiter**（純程式仲裁表）：classify × adequacy → **verdict 六分類** + confidence（內容證據凌駕客訴語氣）。contract_breach 走獨立 C 類履約路徑（需訂單事實；Phase 1 無訂單→強制降級 content_unclear）。
- **L4 diagnose**（純程式）：verdict → recommended_action + owner_role + exec_platform + action_detail（**客服對話當 ground truth，零幻覺**）+ writer_handoff。

**verdict 六分類 → 4 判定層 → action / 執行層**：

| verdict | 歸因域 | 判定層 | action | exec_platform |
|---|---|---|---|---|
| real_config_issue | ①內容 | 第1層 | fix_contradiction | SCM2.0（供應商修正）|
| content_missing | ①內容 | 第2層 | add_missing_info（**writer_handoff=False 強制**）| PM 補事實/修法 |
| content_unclear | ①內容 | 第2層 | clarify_wording | PM/Writer |
| contract_breach | ②履約 | 第3A | penalize_breach | SCM2.0 + ERC 計點（P1 降級）|
| escalate_ops | ③④⑤ | 第3B | escalate_ops | 客服/營運協作 |
| customer_misread | ⑥客人 | — | escalate_ux | UX 洞察（不罰）|

- **防幻覺鐵則**：content_missing 缺的是事實，writer 不可生成；writer_handoff 僅 verdict∈{content_unclear,real_config_issue} 且 suspected_field∈writer 3 欄。
- **信心度路由**：MVP 全 status=new 待人工，蒐 golden 後再放門檻（高信心誤判代價最高，保守）。
- **結構化輸出地基**：Instructor + Pydantic v2 強制 schema + 出格重試。

**依賴**：OpenAI key（已生效 gpt-5-mini）。**驗收**：Promptfoo + golden；先鎖「集合/費用」2 面向打穿再開放 8；第 2/3A/3B 層 Phase 1 先標記不自動執行。

## 3. 落地 delta（code 對齊規劃）

| 項 | 現況 | 需改 |
|---|---|---|
| `schema.py LogicalField` | 7 個（缺 prod_fee/meetup/redeem）| 補齊到 9 邏輯欄位 |
| `schema.py TicketFinding` | 有 verdict 六分類 ✅、缺分類框架欄 | 補 `symptom_tag1/2/3 / trip_stage / root_cause_domain / sub_cause / judgment_tier / severity / responsible_party` |
| `product_refresh.py` | 只撈最新 | 加「版本綁定」模式（下單版本快照 / 退路 A 裁決即快照）|
| `specs/03,04` | 寫「五分類」「撈最新」「出口A/B」| 校正為六分類 / 版本綁定 / RD品控·PM/AM |
| `diagnose` stub | 產出不符 schema Literal → ValidationError | 真 LLM 接線或修 stub |
| packages 表 | =0 | `order_lst.prod_level2_oid` 補 pkg + Sheet 匯入 |

## 4. Roadmap / 依賴 / 風險

| 期間 | 里程碑 | 內容 | gate |
|---|---|---|---|
| M1 | 接入+資料 | Q1 評論 adapter + Q3 **版本資料源探查** + 字面剪枝撈數 | BQ 權限 + 版本源確認 |
| M2 | 判決核心 | Q4 文本→分類貼標 + 兩階段判定 + 六分類仲裁 | OpenAI key ✅ |
| M3 | 可視化 | Q2 分類框架 dashboard（三層下鑽 + 責任歸屬卡）| 需 M2 Finding |
| M4 | 閉環 | golden/eval + 信心度 calibration + 規則缺口回灌 | 需人工 golden |
| P2+ | 擴展 | 工單新 API(6/30) + 行中/供應商管道 + 跨管道聯合判定 | 管道權限/UI |

**風險**：① 版本資料源可能不存在 → 退路「裁決即快照」；② 售前管道 0.01% 假數字 → 以售後為主力；③ 文本→分類精度未知 → 小樣本人工 golden 打基準；④ 第3A 需履約事實 → P1 先標記不自動計點；⑤ 高信心誤判＝錯誤執法 → 門檻保守。

## 5. 待你拍板的決策點

1. **Q3 版本綁定**：KKday 是否有商品/方案內容版本表 or 訂單內容快照？若無，採退路 A（裁決即快照）還是 C（標註最新版裁決）？
2. **Q2 分類框架**：6 歸因域是否定版？是否要把 KKday 既有 tag1/2/3 完整對照表（如 Image#26 的 tag3 全集）做成映射檔？
3. **評論管道**：P2 旁路印證 vs 提前到 P1？
4. **法典欄位數**：58（子7）vs 60（field_codex）定版？
5. 此文檔是否要回寫 Confluence 整合版 V2（同步父子頁）？

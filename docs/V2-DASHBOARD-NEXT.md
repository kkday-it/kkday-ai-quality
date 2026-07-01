# AI 法官 Dashboard — 下一步規格（接續實作用）

> 本檔為清 context 後接續實作的權威規格。參考設計稿 `docs/tickets-dashboard.html`（已存在）+ KKday Tour 五層治理架構 v3（感知層/執行層）。

## 當前狀態（已完成，2026-06-23）
- repo `kkday-ai-quality`（monorepo：backend Python/FastAPI + frontend Vue3/Arco/ECharts）
- 後端評論線端到端走通（錄入→拉取→判決 stub→Finding SQLite），API：`/api/inbound[/upload]` · `/api/diagnose` · `/api/findings[?prod_oid/dimension/verdict]` · `/api/findings/aggregate` · `/api/products` · `PATCH /api/findings/{id}/status`
- 前端兩出口：`/analytics`（出口B）+ `/product`（出口A），FindingCard 共用元件、熱力矩陣下鑽、規則缺口、欄位分組、雙路徑動作
- 路由 **history 模式**（無 #）；vite **port 5273**（config 固定，避開 tour-flow 5173）
- 導航：頂部 Arco Dropdown「AI 商品質檢 ▾」→ 分組「AI 法官」→ 品控分析/單品診斷
- **Tailwind v3 已裝**（devDep tailwindcss@3.4 + postcss + autoprefixer），**尚未配置**（待加 tailwind.config.js preflight:false + postcss.config.js + import css）
- commit 多筆（git log），本地未 push
- 啟動：後端 `cd backend && ./run.sh`（8100）；前端 `cd frontend/apps/console && npx vite`（5273）

## 新 goal 待實作（5 項）

### 1. 導航改「AI 法官 list」結構
- Dropdown「AI 商品質檢 ▾」拉出 **list 含「AI 法官」（暫只一個）**
- 選「AI 法官」→ 顯示其下兩菜單（與 tickets-dashboard.html 一致命名）：
  - **RD／品控 分析**（出口B，route `/analytics`）
  - **PM／AM 單品**（出口A，route `/product`）
- 即：平台 → 支柱(AI法官) → 兩功能。未來加支柱多一個 list 項。

### 2. 菜單命名 + 邏輯對齊 tickets-dashboard.html
- 命名統一改為 **「RD／品控 分析」「PM／AM 單品」**（目前是「品控分析（出口B）」「單品診斷（出口A）」→ 改）
- 邏輯保持與設計稿一致（RD 熱力矩陣進門+下鑽+規則缺口；PM 欄位分組+雙路徑動作）

### 3. 樣式用 Arco + ECharts 豐富化
- 已有熱力矩陣；可再加：趨勢 sparkline、verdict 分布圖、來源管道分布圖（ECharts）
- Tailwind 配置後用 utility 排版（preflight:false 避免破壞 Arco）

### 4. ⭐ 兩面板整合「感知層問題來源」+「執行層平台/角色」
依 KKday Tour 五層治理架構 v3（見 Confluence AI 法官主頁 2105442335 §四/§九 + 感知收集 2109243415）：

**感知層問題來源（intake，要顯示/篩選）**：
- 管道 A｜平台主動詢問：行中關懷 + Feedback
- 管道 B｜客人主動進線：FreshDesk 工單(prod inquiry/客訴) · 訂單訊息+chatbot(dw_kkdb.message/chatbot_messages) · **商品評論**(已實作)
- 管道 C｜供應商主動申訴
- 輔助源：User Feedback System · 商品評論 AI Summary · Mixpanel · NPS
- → schema 需加 `source_channel`(A/B/C) + `source_system`(review/ticket/order_message/feedback…)；兩面板加「來源」維度（篩選/分布圖）

**執行層對應平台/角色（action 落地，要顯示）**：
- 平台：SCM2.0 / Be2（前審非LLM·Danny/Ely）· PM 後台 · 客服系統
- 角色（治理組織）：League Chair(Vertical Head 核可) · Rule Maker(PM Kiki 定門檻/修法) · Coach(AM/BD 承接供應商申訴/落地) · Referee(QC 人工裁決/golden) · Customer Advocate(CS 客訴intake) · Disciplinary(ERC M5計點)
- → 每個 verdict/action 對應「該誰處理(角色)、在哪改(平台)」；FindingCard + 面板顯示對應角色/平台

### 5. Tailwind 配置（已裝待設定）
- `tailwind.config.js`：content `./index.html`,`./src/**/*.{vue,ts}`；`corePlugins:{preflight:false}`（關鍵，避免 reset Arco）
- `postcss.config.js`：tailwindcss + autoprefixer
- `src/style.css`：`@tailwind base/components/utilities`（preflight off 時 base 影響小）；`main.ts` import

## 關鍵檔案
- 導航：`frontend/apps/console/src/App.vue`
- 兩頁：`src/pages/Analytics.vue`(出口B) · `ProductDetail.vue`(出口A)
- 卡片：`src/components/FindingCard.vue`
- API：`src/api/client.ts`
- 後端 schema：`backend/app/core/schema.py`（加 source_channel/source_system + 執行層對應）
- 後端判決：`backend/app/judge/{classify,adequacy,arbiter,diagnose,pipeline}.py`
- 設計稿：`docs/tickets-dashboard.html`（命名/邏輯/卡片雙路徑來源）

## 實作順序建議
1. 後端 schema 加 source_channel/source_system + 執行層 owner_role/exec_platform（diagnose 依 verdict 映射角色/平台）
2. 導航改 AI 法官 list（App.vue）+ 菜單命名對齊
3. Tailwind 配置
4. 兩面板整合來源維度（篩選+ECharts 分布）+ 卡片顯示角色/平台
5. build + 瀏覽器驗證（port 5273）+ commit

# AI 法官 V2 — 主計劃與執行記錄

> 本檔為 repo 自帶的單一權威計劃（從 session plan 落地），含完整規劃 + 今晚執行結果。

## ✅ 執行結果（2026-06-23）— 評論一條線端到端走通

| DoD | 狀態 |
|---|---|
| 評論線端到端跑通（150665 → Finding，stub）| ✅ stub 粗判 5/6，纜車案例判 content_unclear |
| Confluence AI 法官 V2 父 + 5 子頁 | ✅ [父頁 2125660181](https://kkday.atlassian.net/wiki/spaces/VM/pages/2125660181) |
| 本地 commit | ✅ 4 筆（見下）|
| Slack 總結發 C0AQGBRFPCG | ✅ 已發 |

**commit 記錄**（本地，未 push）：
- `bfa0ee6` M0 scaffold + M1a 錄入層
- `207e6ed` M1b 資料拉取 + M2 判決層
- `fa2d65e` README 啟動流程
- `3958568` run.sh + smoke test

**啟動**：`cd backend && ./run.sh`（API）｜`./run.sh test`（smoke）｜詳見 [README](../README.md)

## 最終目標（北極星）
**定期自動**從售前售後進線、評論、工單等系統獲取輸入 → AI 法官判定引擎理解、歸因 → 給出診斷 action → AM/相關人員優化內容 → 提升內容質量、降低售後進線內容類占比。

## 範圍策略
- **MVP（已走通）**：評論一條線端到端（錄入/拉取 → 判定 → action → 結果），150665 真實資料 + LLM stub。
- **後期 roadmap**：售前售後進線、工單等多管道（adapter 模式，下游不動）；定期排程自動跑。
- 大方向 100% 對齊 folder 2117435397（L0–L5 / Finding SSOT / 雙意見仲裁 / 兩出口 / 閉環）。

## 技術棧
後端 Python/FastAPI（沿用 ProductContentAIChecker 判決資產）· 前端 Vue3+Arco+ECharts · monorepo · function-calling（OpenAI SDK tools）· SQLite MVP。

## 判決鏈（評論線）
```
評論(fetch_reviews 150665) → NormalizedTicket → classify(只看客訴) → fetch_product/extract_fields(9欄)
→ adequacy(只看商品原文) → arbiter(純程式仲裁) → diagnose(action+防幻覺) → TicketFinding(SQLite)
```

## 里程碑
- ✅ M1a 錄入層（CSV/Excel/單個 → SQLite）
- ✅ M1b 資料拉取（fetch_reviews/fetch_product，fixture+live）
- ✅ M2-stub 判決層（評論線端到端，LLM stub）
- 🟡 M2 真 LLM + golden 驗收（等 OpenAI key 6/25；先鎖集合/費用 2 dimension）
- ⬜ M3 Dashboard（Arco+ECharts 兩出口 + 表格導入導出）
- ⬜ M4 閉環（Promptfoo golden/eval + 信心度 calibration + 規則缺口回灌）
- ⬜ P2 多管道（order 權限 + 工單 API 6/30 → 聯合判定）

## 風險 / gate
P0 判決準度（stub 先驗流程，key 到換真 LLM + golden）｜ key 6/25 ｜ 評論 production 走內網 Review Service 避 datadome ｜ order/工單多管道為後期 ｜ verify=False 不沿用（新 repo 正規憑證 + token 環境變數）。

## 文檔索引
- 架構 [ARCHITECTURE.md](./ARCHITECTURE.md) · 技術棧 [TECH-STACK.md](./TECH-STACK.md) · 交付 [DELIVERY-PLAN.md](./DELIVERY-PLAN.md)
- 各方面 SD [specs/](./specs/)（00 錄入 · 01 整合 · 02 dashboard · 03 調用 · 04 診斷）
- Confluence V2：[父頁](https://kkday.atlassian.net/wiki/spaces/VM/pages/2125660181)（V2-01～05 子頁）
- V1 來源：[folder 2117435397](https://kkday.atlassian.net/wiki/spaces/VM/folder/2117435397)

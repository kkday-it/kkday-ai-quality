# kkday-ai-product-quality

**AI 法官 — 內容爭議裁決系統**（KKday 內容質量 Pod 第三支柱）

把客訴 / 商品差評 / 工單等真實負面訊號，自動歸因到「哪個商品的哪個欄位該改」，產出可執行 action，並反推「哪條審核規則最該優先」。目標：**降低售後進線的內容類占比**。

> 邏輯參照 [folder 2117435397](https://kkday.atlassian.net/wiki/spaces/VM/folder/2117435397)（工單驅動審核 L0–L5）+ [AI 法官總規則](https://kkday.atlassian.net/wiki/spaces/VM/pages/2105442335)；本 repo 為獨立 Node 實作。

## 技術棧
- **後端**：Node + TypeScript + **Vercel AI SDK**（function calling）+ Hono + Zod
- **前端**：**Vue3** + Vite + **ECharts**（vue-echarts）+ Pinia + Element Plus
- **LLM**：OpenAI gpt-5-mini（可走 AI Gateway）
- **評估**：Promptfoo（golden / LLM-as-judge）
- 完整選型與輪子清單見 [docs/TECH-STACK.md](./docs/TECH-STACK.md)

## 架構
見 [ARCHITECTURE.md](./ARCHITECTURE.md)。核心：**Function-Calling Agent + L0–L5 pipeline**，判決走「兩階段 + 雙意見交叉 + 純程式仲裁」，輸出 5 類 verdict + 信心度路由。

## Monorepo
```
packages/shared   Zod schema + types（TicketFinding / Verdict / Dimension）
packages/server   Node 後端（ingest / datasource / judge / api）
packages/web      Vue3 + ECharts 雙出口 dashboard
fixtures/         golden 測試資料（含 product_150665 纜車案例）
```

## 開發
```bash
pnpm install
pnpm dev          # 前後端並行
pnpm --filter server dev
pnpm --filter web dev
pnpm eval         # Promptfoo golden 驗收
```

## 狀態
M0 scaffold。資料源（評論/商品）已驗證可拉；判決層待實作；LLM 等 OpenAI key（2026-06-25 生效）。

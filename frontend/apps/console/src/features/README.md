# features — 功能模組（feature-based）

每個 feature 自成一夾，內含 `pages/` `components/` `composables/` `utils/` `constants/` `routes/` `types/`
（各有 barrel `index.ts`；對外從夾根 import、內部相對 import）。跨 feature 共用元件在 `src/components/`、
共用 store 在 `src/stores/`、API 層在 `src/api/`、**共用圖表層在 `src/shared/charts/`**（ECharts builders +
圖表契約 + PDF 導出；feature → shared 單向依賴，禁 feature 互相 import）。

| feature | 內容 |
|---|---|
| `judge/` | **AI 法官主模組**：規則配置（RuleManager 版本化編輯：schema 結構規格 / global 判決總規範 / judgment 判決配置（信心閾值·label·G1 auto_confirm 旋鈕）/ C-N 歸因分類 + 歷史對比）、資料上傳、歸因列表（一列一 review + 多歸因堆疊 + 伺服器分頁 + 初判歸因批次 + xlsx 導出）、歸因概覽（縱覽 + 各來源專屬概覽·KPI+漏斗+L1-L3 下鑽+趨勢+PDF 報表導出）。 |
| `settings/` | 設定抽屜：LLM 模型連線 / QC DB 連線（profiles 多套 + 啟用切換）。 |
| `overview/` | 總覽儀表板（config-驅動 DashboardView + chartRegistry；三業務目標 config/presale/postsale）。 |
| `usage/` | 💰 AI 消耗 dashboard（llm_usage per-call 紀錄聚合：成本/token/模型/階段趨勢）。 |
| `auth/` | 登入 / 註冊。 |

技術鐵律（見 `.claude/rules/frontend-vue.md`）：UI＝Arco Design Vue、圖表＝vue-echarts、狀態＝Pinia、
樣式 Tailwind utility-first、按鈕依語義區分主次。

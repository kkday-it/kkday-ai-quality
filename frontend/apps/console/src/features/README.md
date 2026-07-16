# features — 功能模組（feature-based）

每個 feature 自成一夾，內含 `pages/` `components/` `composables/` `utils/` `constants/` `routes/` `types/`
（各有 barrel `index.ts`；對外從夾根 import、內部相對 import）。跨 feature 共用元件在 `src/components/`、
共用 store 在 `src/stores/`、API 層在 `src/api/`、**共用圖表層在 `src/shared/charts/`**（ECharts builders +
圖表契約 + PDF 導出；feature → shared 單向依賴，禁 feature 互相 import）。

| feature | 內容 |
|---|---|
| `judge/` | **AI 法官主模組**：規則配置（RuleManager 版本化編輯：source_mapping 整體配置〔上傳表頭校驗〕/ 判決 Prompt〔polarity + C-1~6 六域，7 支 Prompt-as-Source md〕+ 歷史對比）、資料上傳、歸因列表（一列一 review + 多歸因堆疊 + 伺服器分頁 + 初判歸因批次 + 單筆/批量覆核〔確認/忽略/再點撤銷〕+ 覆核狀態/判決模型篩選 + xlsx 導出（可選「輸出結果版本」＝指定模型的歷史快照，多模型對比）+ `JudgmentHistoryModal` 評論級判決歷史時間軸〔a-timeline：判決快照/覆核轉移/備註三類事件 + 變更徽章 + 評論級備註〕）、歸因概覽（縱覽 + 各來源專屬概覽·KPI+漏斗+L1-L2 下鑽+趨勢+PDF 報表導出+判決模型篩選〔當前判決維度·KPI 文案揭露口徑〕）。 |
| `settings/` | 設定抽屜：LLM 模型連線 / QC DB 連線（profiles 多套 + 啟用切換）。 |
| `overview/` | 總覽儀表板（config-驅動 DashboardView + chartRegistry；三業務目標 config/presale/postsale）。 |
| `usage/` | 💰 AI 消耗 dashboard（llm_usage per-call 紀錄聚合：成本/token/模型/階段趨勢）。 |
| `auth/` | 登入 / 註冊。 |

技術鐵律（見 `.claude/rules/frontend-vue.md`）：UI＝Arco Design Vue、圖表＝vue-echarts、狀態＝Pinia、
樣式 Tailwind utility-first、按鈕依語義區分主次。

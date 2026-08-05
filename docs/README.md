# docs/ — 文檔地圖

## 現行有效文檔（以 code 為準，隨改動同步）

| 文檔 | 內容 |
|---|---|
| 根 [README.md](../README.md) | **唯一權威總覽**：monorepo 結構、啟動、API 一覽、架構要點 |
| [UPSTREAM-REFS.md](./UPSTREAM-REFS.md) | 上游參考 repo（ProductContentAIChecker / ai_review_system）角色、追蹤基線與沿用狀態 |
| [PRD-PROMPT-REVIEW-REVISE.md](./PRD-PROMPT-REVIEW-REVISE.md) | 售後根因 Prompt 調試台的人工評判 × AI 定點改寫閉環：補丁 anchor 驗證、四步流水線抽屜、回歸重跑計分。⚠️ 案例已改存瀏覽器 localStorage、後端不落庫（§2 原落庫設計已加註作廢） |
| `backend/app/**/README.md` | 各模組結構與職責（`app` / `api` / `core` / `core/db` / `core/judge_config` / `core/permissions` / `judge`，共 7 份） |
| [frontend/apps/console/src/features/README.md](../frontend/apps/console/src/features/README.md) | 前端 feature 模組地圖 |
| [config/README.md](../config/README.md) / [constants/README.md](../constants/README.md) | 前後端共用配置 / 常數 SSOT 說明 |
| [scripts/README.md](../scripts/README.md) | 開發腳本索引 |
| [prompts/README.md](../prompts/README.md) | 初判引擎契約（7 支 prompt md 如何被讀取/派生結構）+ 調適閉環操作手冊（編→測→歷史→修→存版） |
| [deploy/README.md](../deploy/README.md) / [docker/README.md](../docker/README.md) | 部署與容器組態說明 |

## Prompt Lab 實驗記錄（2026-07，離線實驗室的當時狀態）

離線實驗室 `scripts/prompt_lab/` + `evals/prompt_lab/` 至今仍在，但下列文檔記錄的是**當時**的
設計、跑批數字與待補缺口，**不是系統現況**；各檔檔頭已標明時間點。線上初判引擎的現況一律以
根 `README.md`、[prompts/README.md](../prompts/README.md) 與 code 為準。

| 文檔 | 內容 | 時間點 |
|---|---|---|
| [PRD-C1-PROMPT-MOCK-EVAL.md](./PRD-C1-PROMPT-MOCK-EVAL.md) | C-1 商品內容單域 Prompt：Layer 1/2 Mock 生成、獨立審核、凍結資料集、評測與回歸比較的技術 PRD | 2026-07-13 |
| [PRD-C1-MOCK-DATA-PIPELINE.md](./PRD-C1-MOCK-DATA-PIPELINE.md) | C-1 判官除錯資料生成與人工判定流程：AI 生成擬真評論 + 人工判定 | 2026-07-13 |
| [C1-PROMPT-LAB-DEV-REPORT.md](./C1-PROMPT-LAB-DEV-REPORT.md) | 上述 Mock 評測實驗室的 Dev 交付報告 + baseline live 實測（§2.5）| 2026-07-13 |
| [PRD-C1-PROMPT-V2.md](./PRD-C1-PROMPT-V2.md) | C-1 判官 Prompt v2 設計與回歸驗證任務書（修 §17.1/17.2/17.3，主攻棄權）| 2026-07-13 |
| [C1-PROMPT-V2-CHANGES.md](./C1-PROMPT-V2-CHANGES.md) | v2 變更說明：三處修改、四輪迭代、Path B preliminary 對比結果（被迫歸因 27.5%→0%）| 2026-07-13 |
| [PRD-C3-C6-MOCK-DATA-WORKFLOW.md](./PRD-C3-C6-MOCK-DATA-WORKFLOW.md) | C-3～C-6 Mock 數據工程：六域泛化、跨域/本域 L2 最小對照、模型隔離、生成審核凍結、Judge 跑批 | 2026-07-15 |
| [HANDOFF-C3-C6-GEMINI-GPT54MINI.md](./HANDOFF-C3-C6-GEMINI-GPT54MINI.md) | 交給外部執行 AI 的完整主 Prompt 逐字存檔（內含當時執行者的機器路徑，重用前需整批置換）| 2026-07-15 |

## archive/ — 封存（僅供追溯，非現行契約）

早期規格與選型記錄，內容已被實作演進推翻（verdict 五分類→純歸因、intake 通用表→5 來源專表、
SQLite 提案→PostgreSQL only 等），**閱讀時勿當現況**：

- `archive/TECH-STACK.md` — 2026-06-22 初期選型記錄
- `archive/specs/01~06-*.md` — 六份早期面向 spec（過時警語見資料夾層級 archive/specs/README.md）

> 規則：新文檔一律先問「根 README 或模組 README 放得下嗎？」；獨立成檔才進 docs/；
> 內容被演進推翻時移入 archive/ 並在此列出，不留在主目錄誤導。

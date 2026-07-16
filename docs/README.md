# docs/ — 文檔地圖

## 現行有效文檔（以 code 為準，隨改動同步）

| 文檔 | 內容 |
|---|---|
| 根 [README.md](../README.md) | **唯一權威總覽**：monorepo 結構、啟動、API 一覽、架構要點 |
| [UPSTREAM-REFS.md](./UPSTREAM-REFS.md) | 上游資料源參照 |
| [PRD-C1-PROMPT-MOCK-EVAL.md](./PRD-C1-PROMPT-MOCK-EVAL.md) | C-1 商品內容單域 Prompt：Layer 1/2 Mock 生成、獨立審核、凍結資料集、評測與回歸比較的可執行技術 PRD |
| [PRD-C1-MOCK-DATA-PIPELINE.md](./PRD-C1-MOCK-DATA-PIPELINE.md) | C-1 判官除錯資料生成與人工判定流程：AI 生成擬真評論 + 人工判定，除錯商品內容單域判官 |
| [C1-PROMPT-LAB-DEV-REPORT.md](./C1-PROMPT-LAB-DEV-REPORT.md) | 上述 Mock 評測實驗室的 Dev 交付報告 + baseline live 實測（§2.5）|
| [PRD-C1-PROMPT-V2.md](./PRD-C1-PROMPT-V2.md) | C-1 判官 Prompt v2 設計與回歸驗證任務書（修 §17.1/17.2/17.3，主攻棄權）|
| [C1-PROMPT-V2-CHANGES.md](./C1-PROMPT-V2-CHANGES.md) | v2 變更說明：三處修改、三輪迭代、Path B preliminary 對比結果（被迫歸因 27.5%→0%）|
| [PRD-C3-C6-MOCK-DATA-WORKFLOW.md](./PRD-C3-C6-MOCK-DATA-WORKFLOW.md) | C-3～C-6 Mock 數據工程：六域泛化、跨域/本域 L2 最小對照、模型隔離、生成審核凍結、Judge 跑批、報告與可交付其他 AI 的任務包 |
| [HANDOFF-C3-C6-GEMINI-GPT54MINI.md](./HANDOFF-C3-C6-GEMINI-GPT54MINI.md) | 可直接交給執行 AI 的完整主 Prompt：Gemini 五輪 Mock、獨立 Auditor、GPT-5.4-mini high reasoning baseline、四域 Excel 與彙總交付 |
| `backend/app/**/README.md` | 各模組結構與職責（api / core / core/db / core/judge_config / judge） |
| `frontend/.../features/README.md` | 前端 feature 模組地圖 |
| [config/README.md](../config/README.md) / [constants/README.md](../constants/README.md) | 前後端共用配置 / 常數 SSOT 說明 |
| [scripts/README.md](../scripts/README.md) | 開發腳本索引 |
| [prompts/README.md](../prompts/README.md) | 判決引擎契約（7 支 prompt md 如何被讀取/派生結構）+ 調適閉環操作手冊（編→測→歷史→修→存版） |
| [類別定義_V0.1.md](./類別定義_V0.1.md) | 六域判準人讀版（C1 交付物）：由 `scripts/tools/gen_taxonomy_doc.py` 從 `prompts/*.md` **單向生成**，改判準請改 prompt md 後重新產生，不得手改本檔 |

## archive/ — 封存（僅供追溯，非現行契約）

早期規格與選型記錄，內容已被實作演進推翻（verdict 五分類→純歸因、intake 通用表→5 來源專表、
SQLite 提案→PostgreSQL only 等），**閱讀時勿當現況**：

- `archive/TECH-STACK.md` — 2026-06-22 初期選型記錄
- `archive/specs/01~06-*.md` — 六份早期面向 spec（過時警語見資料夾層級 archive/specs/README.md）

> 規則：新文檔一律先問「根 README 或模組 README 放得下嗎？」；獨立成檔才進 docs/；
> 內容被演進推翻時移入 archive/ 並在此列出，不留在主目錄誤導。

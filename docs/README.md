# docs/ — 文檔地圖

## 現行有效文檔（以 code 為準，隨改動同步）

| 文檔 | 內容 |
|---|---|
| 根 [README.md](../README.md) | **唯一權威總覽**：monorepo 結構、啟動、API 一覽、架構要點 |
| [UPSTREAM-REFS.md](./UPSTREAM-REFS.md) | 上游資料源參照 |
| [PRD-C1-PROMPT-MOCK-EVAL.md](./PRD-C1-PROMPT-MOCK-EVAL.md) | C-1 商品內容單域 Prompt：Layer 1/2 Mock 生成、獨立審核、凍結資料集、評測與回歸比較的可執行技術 PRD |
| [C1-PROMPT-LAB-DEV-REPORT.md](./C1-PROMPT-LAB-DEV-REPORT.md) | 上述 Mock 評測實驗室的 Dev 交付報告 + baseline live 實測（§2.5）|
| [PRD-C1-PROMPT-V2.md](./PRD-C1-PROMPT-V2.md) | C-1 判官 Prompt v2 設計與回歸驗證任務書（修 §17.1/17.2/17.3，主攻棄權）|
| [C1-PROMPT-V2-CHANGES.md](./C1-PROMPT-V2-CHANGES.md) | v2 變更說明：三處修改、三輪迭代、Path B preliminary 對比結果（被迫歸因 27.5%→0%）|
| `backend/app/**/README.md` | 各模組結構與職責（api / core / core/db / core/judge_config / judge） |
| `frontend/.../features/README.md` | 前端 feature 模組地圖 |
| [config/README.md](../config/README.md) / [constants/README.md](../constants/README.md) | 前後端共用配置 / 常數 SSOT 說明 |
| [scripts/README.md](../scripts/README.md) | 開發腳本索引 |

## archive/ — 封存（僅供追溯，非現行契約）

早期規格與選型記錄，內容已被實作演進推翻（verdict 五分類→純歸因、intake 通用表→5 來源專表、
SQLite 提案→PostgreSQL only 等），**閱讀時勿當現況**：

- `archive/TECH-STACK.md` — 2026-06-22 初期選型記錄
- `archive/specs/01~06-*.md` — 六份早期面向 spec（各檔頂部已標過時警語）

> 規則：新文檔一律先問「根 README 或模組 README 放得下嗎？」；獨立成檔才進 docs/；
> 內容被演進推翻時移入 archive/ 並在此列出，不留在主目錄誤導。

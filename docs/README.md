# docs/ — 文檔地圖

## 現行有效文檔（以 code 為準，隨改動同步）

| 文檔 | 內容 |
|---|---|
| 根 [README.md](../README.md) | **唯一權威總覽**：monorepo 結構、啟動、API 一覽、架構要點 |
| [UPSTREAM-REFS.md](./UPSTREAM-REFS.md) | 上游資料源參照 |
| `backend/app/**/README.md` | 各模組結構與職責（api / core / core/db / core/judge_config / judge） |
| `frontend/.../features/README.md` | 前端 feature 模組地圖 |
| [config/README.md](../config/README.md) / [constants/README.md](../constants/README.md) | 前後端共用配置 / 常數 SSOT 說明 |
| [scripts/README.md](../scripts/README.md) | 開發腳本索引 |
| [類別定義_V0.1.md](./類別定義_V0.1.md) | 六域判準人讀版（C1 交付物）：由 `scripts/tools/gen_taxonomy_doc.py` 從 `prompts/prompts/*.md` **單向生成**，改判準請改 prompt md 後重新產生，不得手改本檔 |

## archive/ — 封存（僅供追溯，非現行契約）

早期規格與選型記錄，內容已被實作演進推翻（verdict 五分類→純歸因、intake 通用表→5 來源專表、
SQLite 提案→PostgreSQL only 等），**閱讀時勿當現況**：

- `archive/TECH-STACK.md` — 2026-06-22 初期選型記錄
- `archive/specs/01~06-*.md` — 六份早期面向 spec（各檔頂部已標過時警語）

> 規則：新文檔一律先問「根 README 或模組 README 放得下嗎？」；獨立成檔才進 docs/；
> 內容被演進推翻時移入 archive/ 並在此列出，不留在主目錄誤導。

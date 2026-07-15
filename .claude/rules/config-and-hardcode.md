---
paths:
  - "config/**"
  - "backend/app/**/*.py"
  - "**/*.constant.ts"
  - "**/constants/**"
  - "start.sh"
  - "docker-compose*.yml"
---

# 配置化規範（禁硬編碼 · 編輯 config / 後端 / constants 時載入）

**核心原則：業務會調的值、跨環境會變的值、前後端共用的值，一律不准寫死在代碼裡。** 寫死＝埋雷：非工程師要調一個閾值得改碼發版、前後端各寫一份必然 drift。動手寫任何常數前，先過下方決策樹判斷去處。

## 三層 config 決策樹（新增常數必過）

| 值的性質 | 去處 | 載入方式 |
|---|---|---|
| **機密**（token / 密碼 / private key）| `backend/.env`（Pydantic `Settings`）| `config.py`，同名大寫 env var 覆蓋 |
| **跨環境會變的非機密**（DB URL / CORS origins / port / project id / timeout / 並發數）| `backend/.env`（有 dev default）| `config.py` |
| **前後端共用「業務可調」非機密**（模型清單 / provider 目錄 / QC 連線預設 / 顯示 label / 定價）| `config/global/*.json` | 後端 `settings.py`、前端 `@config/global/*` **同讀一份**（SSOT）|
| **判決領域**（verdict / L1-L2 判準 / 來源欄位映射 / 信心分層閾值）| `config/ai_judge/*.json` | 後端 lazy load、前端 import 同一 JSON |
| **前後端共用「固定參照」常數**（enum / 代碼字典，如 traveller_type 代碼→文案、狀態碼→中文，非業務可調）| `constants/<維度>/*.json`（repo 根，按維度分子資料夾）| 後端 `paths.CONSTANTS_DIR`、前端 `@constants/<維度>/*` **同讀一份**（SSOT）|
| **純前端 UI**（Arco 色 token / 分頁大小 / 輪詢間隔 / 元件私有常數）| `frontend/.../features/*/constants/*.constant.ts` | barrel `index.ts` 出口 |

> **config/ vs constants/（repo 根兩大共用目錄，前後端同源）**：
> - `config/`＝**業務/QC 會調**的值（規則樹、閾值、model 清單、連線預設）——非工程師改、可版本化、變動較頻。
> - `constants/`＝**固定參照**常數（enum、代碼→文案字典、對照表）——工程師維護、變動低頻、通常來自外部權威來源（如 kkday-member-ci）。按**維度**分子資料夾（如 `constants/labels/`）。
> - 兩者皆前後端同讀同一份 JSON：前端 `@config` / `@constants` alias，後端 `paths.CONFIG_DIR` / `paths.CONSTANTS_DIR`。

> **SSOT 鐵律**：同一語義的值（如 verdict 中文 label、來源清單、模型名、代碼字典）**只准有一份真相源**。前端顯示 verdict label → 讀 `config/ai_judge/verdicts.json`；顯示 traveller_type 文案 → 讀 `constants/labels/*.json`，**禁止**在前端另寫一份翻譯。前後端都用到 → 進 `config/` 或 `constants/`，禁各寫一份。

## 一鍵啟動引導鐵律（start.sh 零配置）

**專案統一配置（token / 機密 / 必要 env var）必須能被 `./start.sh`（dev）與 `./start.sh prod` 一鍵配置到位**——任何模式的啟動都不得要求使用者先手動 export / 編輯檔案才能起服務。新增配置時按性質接入：

| 新增的配置性質 | 接入義務 |
|---|---|
| **可自動生成的機密**（隨機值即可：簽名 secret / 加密 key / DB 密碼）| `start.sh` prod 段補一行 `_ensure_secret <KEY> <bytes>`（hex 生成·冪等），並在 `.env` 模板 heredoc 加註解 |
| **外部核發 token**（如 `OPENAI_API_KEY`，無法自動生成）| 必須設計為**可空啟動**的選填 fallback（compose 用 `${VAR:-}`）；主路徑走前端「設定」面板加密落庫，`.env` 模板加選填註解 |
| **跨環境非機密**（port / timeout / URL）| `config.py` 給 dev default（dev 零配置天然成立）；prod 需覆蓋者在 compose `environment` 給 `${VAR:-預設}` |

- compose 的 `:?` 必填變數 ↔ `start.sh` 的 `_ensure_secret` **必須一一對應**：新增 `:?` 變數而未接 start.sh 引導＝破壞一鍵啟動，禁止
- 引導**冪等鐵律**：已有非空值的 key 永不重新生成（重生 `AIQ_SECRET_KEY`＝庫內密文永久解不開、重生 `POSTGRES_PASSWORD`＝連不上既有 pgdata）
- 文件（README / docker/README）**禁止**出現「啟動前請先手動設定 X」的必要前置步驟；選填功能（如 LLM fallback token）除外

## 允許保留代碼內的例外（過度工程防禦）

專案**反對 over-engineering**——以下值**留代碼內**，強行 config 化反增複雜度：

- **框架 / 協議常數**：HTTP status code、bcrypt 72 bytes 上限、SHA 截位長度等演算法規範
- **一次性內部實作細節**：pagination 繞過用的技術 magic number、PDF render scale 等調教過的排版常數（提取為**檔內具名常數 + 註解**即可，不外部化）
- **元件私有樣式 / Tailwind class**：不跨檔案共用者
- **error display UX 上限**（如錯誤最多顯示 8 條）：不跨環境變化

> 判準：這個值**跨環境會變嗎？業務會調嗎？非工程師需要改嗎？前後端都用嗎？** 四問任一為「是」→ config 化；全「否」→ 留代碼內（但仍需具名 + 註解，禁裸 magic number）。

## 新增常數自問清單（寫死前必答）

1. 這個值 codebase / config / constants 裡已經有一份了嗎？→ 有則復用，禁平行造第二份
2. 前後端都會用到嗎？→ 是則進 repo 根共用目錄（**業務可調** → `config/`；**固定參照/字典** → `constants/<維度>/`），單一真相源
3. 是機密或跨環境值嗎？→ 是則進 `.env`
4. 業務 / QC 主管將來要調嗎？→ 是 → `config/`；否但前後端共用的固定對照 → `constants/`
5. 以上皆否（純前端 UI）→ `features/*/constants/*.constant.ts`；再否則留代碼內，但**提取為具名常數 + 註解**，禁裸 magic number

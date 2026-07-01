---
paths:
  - "config/**"
  - "backend/app/**/*.py"
  - "**/*.constant.ts"
  - "**/constants/**"
---

# 配置化規範（禁硬編碼 · 編輯 config / 後端 / constants 時載入）

**核心原則：業務會調的值、跨環境會變的值、前後端共用的值，一律不准寫死在代碼裡。** 寫死＝埋雷：非工程師要調一個閾值得改碼發版、前後端各寫一份必然 drift。動手寫任何常數前，先過下方決策樹判斷去處。

## 三層 config 決策樹（新增常數必過）

| 值的性質 | 去處 | 載入方式 |
|---|---|---|
| **機密**（token / 密碼 / private key）| `backend/.env`（Pydantic `Settings`）| `config.py`，同名大寫 env var 覆蓋 |
| **跨環境會變的非機密**（DB URL / CORS origins / port / project id / timeout / 並發數）| `backend/.env`（有 dev default）| `config.py` |
| **前後端共用的非機密**（模型清單 / provider 目錄 / QC 連線預設 / 顯示 label / 定價）| `config/global/*.json` | 後端 `settings.py`、前端 `@config/global/*` **同讀一份**（SSOT）|
| **判決領域**（verdict / L1-L3 判準 / 來源欄位映射 / 信心分層閾值）| `config/ai_judge/*.json` | 後端 lazy load、前端 import 同一 JSON |
| **純前端 UI**（Arco 色 token / 分頁大小 / 輪詢間隔 / 元件私有常數）| `frontend/.../features/*/constants/*.constant.ts` | barrel `index.ts` 出口 |

> **SSOT 鐵律**：同一語義的值（如 verdict 中文 label、來源清單、模型名）**只准有一份真相源**。前端顯示 verdict label → 直接讀 `config/ai_judge/verdicts.json` 的 `label_zh`，**禁止**在前端 constants 另寫一份翻譯。前後端都用到 → 進 `config/global/` 或 `config/ai_judge/`，禁各寫一份。

## 允許保留代碼內的例外（過度工程防禦）

專案**反對 over-engineering**——以下值**留代碼內**，強行 config 化反增複雜度：

- **框架 / 協議常數**：HTTP status code、bcrypt 72 bytes 上限、SHA 截位長度等演算法規範
- **一次性內部實作細節**：pagination 繞過用的技術 magic number、PDF render scale 等調教過的排版常數（提取為**檔內具名常數 + 註解**即可，不外部化）
- **元件私有樣式 / Tailwind class**：不跨檔案共用者
- **error display UX 上限**（如錯誤最多顯示 8 條）：不跨環境變化

> 判準：這個值**跨環境會變嗎？業務會調嗎？非工程師需要改嗎？前後端都用嗎？** 四問任一為「是」→ config 化；全「否」→ 留代碼內（但仍需具名 + 註解，禁裸 magic number）。

## 新增常數自問清單（寫死前必答）

1. 這個值 codebase / config 裡已經有一份了嗎？→ 有則復用，禁平行造第二份
2. 前後端都會用到嗎？→ 是則進 `config/global/` 或 `config/ai_judge/`（單一真相源）
3. 是機密或跨環境值嗎？→ 是則進 `.env`
4. 業務 / QC 主管將來要調嗎？→ 是則 config 化
5. 以上皆否 → 留代碼內，但**提取為具名常數 + 註解說明「為什麼是這個值」**，禁散落 inline magic number

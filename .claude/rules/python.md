---
paths:
  - "**/*.py"
---

# Python 規則（編輯 .py 時載入）

## 註釋規範（Google-style docstring · 強制）

module / 公開 function / class **必加** `"""..."""`：

- 一句話用途 + `Args:` / `Returns:` / `Raises:`（會拋錯時）
- 型別走 type hint，docstring **不重複型別**、專注語義與動機
- 複雜邏輯行內 `#` 註解說明「為何這樣做」，而非複述代碼字面
- 禁止用註釋掩蓋壞代碼（該重構就重構）；TODO / FIXME 須附原因或追蹤票號；中文註釋，技術術語 / API 名保留英文

> 本專案既有代碼註釋密度高（如 `client.py` ping / `pipeline.py` 各閘門皆有「為何」註解）——新增 / 修改代碼比照既有密度，勿降低。

## 重庫 lazy import（預設機制）

重量級 / 選用依賴（openai / 大型 SDK / 資料處理庫等）**不在 module 頂 import**，改在實際用到的函式內 import，加速啟動並降低未用路徑的載入 / 記憶體成本：

- 判準：**每次 import 本模組都需要它嗎？** 否（僅特定函式 / 選用功能用）→ 函式內 import（見 `client.py` 的 `from openai import OpenAI` 置於函式內）
- config / 資料 / engine 採 lazy load + 模組級快取（首次存取才載，見既有 `ai_judge` / `pricing` / `source_mapping` / `tables` engine pattern；編輯後 `reload()` 清快取）
- 例外：輕量標準庫、模組核心必用者留頂部（強行下推反增雜訊）

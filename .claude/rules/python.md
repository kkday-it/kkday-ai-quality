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

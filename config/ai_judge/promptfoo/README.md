# 商品評論歸因 L3 分類回歸測試（promptfoo）

用各 L3 判準裡現成的 `positive_cases`（真實評論句）建回歸矩陣，改動 rule 判準或 prompt 後一鍵驗「哪些 L3 命中率掉了」，防界線漂移。這是「嚴格歸類三件套」的第 3 件（前兩件＝Structured Outputs enum + evidence 逐字反查，已在 `prejudge.py` / `client.py` 落地）。

## 檔案
- `promptfooconfig.yaml` — promptfoo 設定：provider + 測試集引用。
- `provider.py` — Python provider，直接呼叫 `app/judge/prejudge.to_finding`（複用線上判準，不另寫分類邏輯），回判到的 `l3_code`。
- `tests.json` — 由 `scripts/gen_promptfoo_tests.py` 從 `rule_C-*.json` 的 positive_cases 自動生成，勿手改。

## 跑法
```bash
# 1) 由 rule 檔重生測試集（rule 判準改過就重跑）
python scripts/gen_promptfoo_tests.py

# 2) 執行回歸（需 node；provider 透過 backend venv 呼叫真引擎）
cd config/ai_judge/promptfoo
npx promptfoo@latest eval
npx promptfoo@latest view      # 命中率報表
```

## 前置
- backend venv 可 `import app.*`（於 repo 根有 `backend/.venv`）。
- 一組真 token 的 user：`provider.py` 預設取第一個有 `provider_token` 的 user；或 `export PROMPTFOO_USER_ID=<uid>` 指定。

## ⚠️ 成本
每條 positive_case 打一次真 LLM（數量＝positive_cases 總數，約數百條）。有 token 成本，非免費 CI；建議判準大改後或發版前跑，非每次 commit。

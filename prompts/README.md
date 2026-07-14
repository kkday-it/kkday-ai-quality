# docs/prompts/ — 判決引擎契約 + 調適閉環操作手冊

`prompts/*.md`（7 支：`00_polarity` + `01_C-1`~`06_C-6`）是判決引擎的**唯一真相源**
（Prompt-as-Source 架構）——判準文字、六域分類結構、L2 面向目錄皆由此派生，禁止另存平行副本。
本檔說明：① 引擎如何讀取這 7 支檔 ② 調適（改判準）時的標準操作流程。

> 2026-07-13：舊「評測交付 Prompt 包 v3」靜態快照機制（`gen_eval_prompt_pack.py` 生成
> `bundle.json`/`manifest.json`）已隨 Prompt-as-Source 全面重構退役並移除——引擎改為**即時讀取**
> （`prompt_source.load()`：DB 熱編 active 版優先→本檔案 fallback），不需要、也不應該再產生靜態快照
> 比對。本檔取代原本描述該快照機制的舊版說明。

## 引擎契約（判決程式碼如何讀這 7 支檔）

- **格式**：每支檔固定三節——`## System`（judge 人設 + `<domain_map>`/`<domain_boundary>`/
  `<facet_catalog>` 等 XML 標籤區塊）、`## User`（模板，含 `{TEXT}`；域 prompt 另需 `{POLARITY}`）、
  `## Schema`（該支輸出 JSON Schema；域 prompt 的 `attributions[].l2_code` enum）。
- **載入層**：`backend/app/judge/prompt_source.py` 的 `load(prompt_id)`——DB（`judge_rule_versions`
  的 `prompt_polarity`/`prompt_C-1~6`，RuleManager「初判 Prompt」熱編）優先，缺 active 版時 fallback
  讀本目錄檔案；模組級快取，存檔後 `reload()` 清空。
- **結構派生**：`structure()` 從六域 prompt 派生分類結構——域機器值＝檔名尾綴（`01_C-1_content`→
  `content`）、L2 面向＝各域 `<facet_catalog>`「■ CODE LABEL：定義」解析、域中文名/建議動作/負責
  單位＝`config/ai_judge/domains.json`（唯一無法從 prompt 推導的域層業務 metadata）。
  `app/core/judge_config/ai_judge.py` 讀 `structure()` 建索引，供 15 個消費端使用，不再讀 DB 樹。
- **自洽護欄**：`validate(text, prompt_id)`（存檔前置閘門）驗三節可解析 + Schema 合法 + User 含
  `{TEXT}` + 域 prompt 的 `facet_catalog` codes **==** Schema `l2_code` enum（判準與結構同源，
  任一漂移即拒存）。
- **判決引擎**：`prejudge.py` 的 `_attrs_pack`——極性閘門（`00_polarity`）→ 六域 prompt **並行**
  各自判斷是否命中該域 → 合流去重排序 + 信心閘門（`prejudge._gate_attrs`）。

## 調適閉環操作手冊（編 → 測 → 歷史 → 修 → 存版）

```
RuleManager「初判 Prompt」md 編輯 ──存檔（validate 自洽驗證）──▶ 新版本（append-only）
        │                                                              │
        ▼                                                              │
   兩種測試入口（皆走診斷理由 overlay：命中附 reason，棄權附 abstain_reason）
   ① 歸因列表逐列「測試」→ RowPromptTestModal：對這一則跑七支 prompt，六域裁決逐域交代
   ② 歸因列表工具列「測試 Prompt」→ PromptEvalModal：抽 N 則現行判決 or
      當前篩選子集（B1 filters）快測單支 → 指標卡 + 分歧表
        │
        ▼
   測試歷史（B2 `prompt_eval_runs`，PromptEvalModal「測試歷史」摺疊區）
   ——同 prompt_id 依時間查歷次結果，改 prompt 前後指標可比；CLI `eval_prompt_single.py --compare`
     做逐案 improvements/regressions diff
        │
        ▼
   依分歧理由定位問題：邊界寫糊（改 `<domain_boundary>`）／例句缺（補 `<facet_catalog>` 正反例）／
   facet 錯位（調整 code 對應）→ 回頭改 prompt md → 重測 → 達標後存版
```

### 兩個測試入口如何選

| 情境 | 用哪個 |
|---|---|
| 手邊這一則判得怪怪的，想知道為什麼 | ① 歸因列表「測試」（單條，六域逐一交代） |
| 改完某支 prompt，想知道對現行資料的整體影響 | ② PromptEvalModal（可選「僅測當前篩選子集」） |

## 相關檔案

| 檔案 | 用途 |
|---|---|
| `prompts/*.md`（7 支） | 唯一真相源，見上方引擎契約 |
| `BASELINE.md` | 7 支 prompt 的基線指標快照（`eval_prompt_single.py` 量測）：調任一支後重跑 `--n 20` 對比，±0.05~0.10 屬 run-to-run 噪音帶 |
| `../../scripts/tools/eval_prompt_single.py` | CLI 單支評測 harness（production 現行判決參照集，`--compare` A/B、`--repeats` 穩定度） |
| `../../scripts/tools/gen_taxonomy_doc.py` | 從本目錄 7 支 md 生成人讀版 `../類別定義_V0.1.md`（單向：prompts→文檔） |

## 侷限

- 診斷理由（reason/abstain_reason）為**測試專用 overlay**（`app/judge/prompt_eval.py` 評測期動態
  附加 schema 欄位），不寫入本目錄 md、production 判決路徑零影響；若日後驗證 reason 對判準本身
  有幫助，可考慮正式寫進 md（v2 觀察項）。
- md 是**判準文字**唯一源；`config/ai_judge/domains.json`（域層業務 metadata）仍是獨立小檔，因其
  內容無法從 prompt 自然推導，不算「另存判準副本」。

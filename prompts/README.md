# prompts/ — 初判引擎契約 + 調適閉環操作手冊

`prompts/<prompt_id>/`（7 支：`00_polarity` + `01_C-1_content`~`06_C-6_customer`）是初判引擎的
**唯一真相源**（Prompt-as-Source 架構）——判準文字、六域分類結構、面向目錄皆由此派生，禁止另存平行副本。
本檔說明：① 版本庫佈局 ② 引擎如何讀取 ③ 調適（改判準）時的標準操作流程。

## 版本庫佈局（2026-07-28 起）

```
prompts/01_C-1_content/
  ACTIVE                 單行純文字＝當前生效的版本名（唯一決定線上跑哪一版）
  v20260724041913.md     一版一檔全文快照，immutable；frontmatter 以 HTML 註解夾帶 author/note
  v20260728153000.md
prompts/drafts/          未入庫的編輯中內容（已入 .gitignore，不進版本控制）
```

- **生效版由 `ACTIVE` 指標檔決定，不是「檔名最大的那個」**。售後調試台（`prompts/debug/`）採檔名
  字典序，2026-07-28 當天就因此連續三次靜默互蓋；顯式指標讓「哪版生效」與「誰最後存檔」解耦，
  切回舊版也成為明確動作。
- **並發安全來自 `expected_base_version` 樂觀鎖，不是來自「檔案進了 git」**。實測顯示同分支循序
  commit 屬 fast-forward，git 從不因語意互蓋而報衝突。存檔前基線不符即 409，這道檢查不可移除。
- 版本識別為 `v` + 14 位 **UTC** 時間戳（定長，故字典序即時序，可直接比大小判新舊）。

> 引擎採**即時讀取**（`prompt_source.load()` 讀 `ACTIVE` 指向的版本），不產生靜態快照比對。
> 線上跑的就是 git 上看得到的那一份——舊架構的 DB 熱編層（`judge_rule_versions` 的 `prompt_*`）
> 已於 2026-07-28 全面退役，它正是本檔宣稱「禁止另存平行副本」卻實際存在的那份平行副本。

## 引擎契約（初判程式碼如何讀這 7 支檔）

- **格式**：域 prompt 固定四節、polarity 三節——`## System`（judge 人設 + `<facet_catalog>` 例句 +
  `<domain_boundary>` 判準例句；**模型面向**）、`## User`（模板，含 `{TEXT}`；域 prompt 另需 `{POLARITY}`）、
  `## Taxonomy`（```json 域分類樹；**機器面向、域 prompt 專有**）、`## Schema`（該支輸出 JSON Schema；域
  prompt 的 `attributions[].l2_code` **不手寫 enum**——由 `## Taxonomy` 派生注入）。
- **`## Taxonomy`＝分類唯一源**（```json，與 `## Schema` 同機器契約、同圍欄解析，**不送 LLM**）：域節點
  `{code(域機器值), label, action, owner, evidence_gated, children:[...]}`；children 為 facet 節點，可再巢狀
  `children`＝**可變深度**。分類的**類別＋層級＋域 metadata＋證據閘**全在此，程式碼零 taxonomy 假設。改
  prompt → `reload()` → 全套（篩選樹/enum/域 meta）即時跟著換。模型判分類靠 `## System` 的 `<facet_catalog>`
  例句（decision_process 明示 l2_code 從 facet_catalog 選），故 `## Taxonomy` 只餵機器、不進模型 context。
- **載入層**：`prompt_source.load(prompt_id)`——讀 `prompts/{id}/ACTIVE` 指向的版本（缺 ACTIVE
  或指標懸空一律 fail-loud，不靜默 fallback）；模組級快取，存檔後 `reload()` 清空。域 prompt load
  時把 `## Taxonomy` 派生的 code 注入 Schema 的 `l2_code.enum`。版本庫本體見 `app/judge/prompt_versions.py`。
- **結構派生**：`structure()` 從各域 `## Taxonomy` 派生 `{domain, domain_label, action, owner,
  evidence_gated, facets, tree}`（域機器值＝檔名尾綴）。`ai_judge` 讀 `structure()` 建索引供消費端；
  `evidence_gated` 域集合供 `prejudge` 證據封頂（取代舊 config 的 evidence_gated_domains）。
  域層 metadata 全進 `## Taxonomy` root。
- **護欄**：`validate` 驗各節可解析 + Schema 合法 + User 含 `{TEXT}` + 域 prompt `## Taxonomy` 可解析且
  至少一 facet。（enum 由 taxonomy 派生，先天無 drift，故無 facet==enum 護欄。）
- **初判引擎**：`prejudge.py` 的 `_attrs_pack`——極性閘門（`00_polarity`）→ 六域 prompt **並行**
  各自判斷是否命中該域 → 合流去重排序 + 信心閘門（`prejudge._gate_attrs`）。

## 調適閉環操作手冊（編 → 測 → 歷史 → 修 → 存版）

```
RuleManager「初判 Prompt」md 編輯 ──存檔（validate + 基線比對）──▶ 新版本檔 ＋ ACTIVE 更新
        │                                                              │
        ▼                                                              │
   歸因列表「Prompt 測試」沙盒抽屜（PromptSandboxDrawer，走診斷理由 overlay：命中附 reason，
   棄權附 abstain_reason）：對單列 / 勾選多筆 / 依條件批量選取，跑勾選的 prompt 子集，ungated
        │
        ▼
   測試歷史（獨立 `prompt_sandbox_runs` 表，抽屜「測試歷史」分頁）
   ——與正式初判歷史完全分離，捕捉完整 LLM log 供事後回看；CLI `eval_prompt_single.py --compare`
     另供逐案 improvements/regressions diff
        │
        ▼
   依分歧理由定位問題：加/改類別（改 `## Taxonomy`）／邊界寫糊（改 `<domain_boundary>`）／例句缺（補 `<facet_catalog>` 正反例）／
   facet 錯位（調整 code 對應）→ 回頭改 prompt md → 重測 → 達標後存版
```

## 相關檔案

| 檔案 | 用途 |
|---|---|
| `prompts/<id>/v*.md`（7 支各自的版本庫） | 唯一真相源，見上方引擎契約與版本庫佈局 |
| `BASELINE.md` | 7 支 prompt 的基線指標快照（`eval_prompt_single.py` 量測）：調任一支後重跑 `--n 20` 對比，±0.05~0.10 屬 run-to-run 噪音帶 |
| `../../scripts/tools/eval_prompt_single.py` | CLI 單支評測 harness（production 現行初判參照集，`--compare` A/B、`--repeats` 穩定度） |
| `debug/after_sales_root_cause/*.md` | **另一條線**（與上方 7 支初判 prompt 無關）：售後根因調試台的 Prompt 版本庫，時間戳一版一檔全文快照，最新版即線上口徑；`CHANGELOG.md` 記每版的誤判案與改法。載入見 `app/judge/prompt_debug_versions.py` |
| `debug/reviser.md` | 調試台「AI 定點改寫」助手的 system prompt（`app/judge/prompt_reviser.py` 讀取，熱掛載存檔即生效）：規定模型只出定點補丁、anchor 須逐字複製且唯一、不得刪既有判例庫與校準層。這份寫壞＝改寫的安全帶失效，改動請連帶跑 `tests/test_prompt_reviser.py` |

## 侷限

- 診斷理由（reason/abstain_reason）為**測試專用 overlay**（`app/judge/prompt_eval.py` 評測期動態
  附加 schema 欄位），不寫入本目錄 md、production 初判路徑零影響；若日後驗證 reason 對判準本身
  有幫助，可考慮正式寫進 md（v2 觀察項）。
- md（含 `## Taxonomy`）是分類**類別＋層級＋域 metadata＋證據閘**唯一源（中文名/action/owner 全進各域 `## Taxonomy` root）。

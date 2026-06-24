# vendored — ProductContentAIChecker 沿用資產清單

從 `ProductContentAIChecker`（審品/撰寫專案）重整進 AI 法官的判決邏輯文件。AI 法官為獨立項目，
**唯一沿用其已驗證 prompt / 規則作判斷依據**，判決鏈 L1-L5 自建。逐字搬入（無失真），
provenance 與面向對照如下。搬入日期 2026-06-24。

## 一、判決深度 prompt（`judge_prompts/`）— 直接用

| 本檔 | 來源（ProductContentAIChecker） | 行數 | 對應面向 / 欄位 | 結構重點 | 沿用方式 |
|---|---|---|---|---|---|
| `行程流程_G1G3.md` | `promptVersion/prompt-20260430182208.md` | 560 | 行程流程（prod_schedules / pkg_schedules / prod_summary） | G1-1/G1-2/G3-1/G3-2 四碼 DEF + 排除項 + 12 few-shot + Output Schema | 注入 `adequacy._real()` 作該面向 system prompt |
| `時效過期_GEN1.md` | `prompts/general/latest.md` | 645 | 限制與風險（時效/過期，跨欄位 9 欄掃描） | 7 鐵則(舉證責任在 Failed 方) + Pattern A/B/C + 41 few-shot + 10 STOP 預檢 + 10 強制自問 | 注入 adequacy 作「時效爭議裁決」；`{TODAY}` 由系統注入 |
| `商品名稱_judge_v2.md` | `judge-prompt-v2-商品名稱.md` | 113 | 商品定位 / prod_name | 機器前置(空/長度/禁詞)→子維度評分(4 維權重)→事實安全網 三段式 | 注入 adequacy；亦為其他欄位裁判的範本框架 |

## 二、撰寫生成 prompt（`writer_prompts/`）— 改造後用（抽 criteria，非直接當裁判）

| 本檔 | 來源 | 行數 | 可抽取作裁判依據的段落 |
|---|---|---|---|
| `product_name.md` | `ai_writer_mvp/promptVersion/product_name/prompt-20260604123808.md` | 231 | 法典條文 / 命名結構 / 允許 / 禁止 → 填 `field_codex` 對應欄位 |
| `highlights.md` | `ai_writer_mvp/promptVersion/highlights/prompt-20260603110829.md` | 427 | 4 句結構定義 / 事實可驗證標準 / 機器可校驗規則 |
| `description.md` | `ai_writer_mvp/promptVersion/description/prompt-20260603110825.md` | 632 | **事實三層分級 A/B/C + 防幻覺特別規則** → evidence_quote / ground_truth 幻覺防線 |

## 三、規則資料 + 確定性檢查（直接用）

| 本檔 | 來源 | 內容 | 沿用方式 |
|---|---|---|---|
| `writer_rules.json` | `ai_writer_mvp/rules.json`（逐字 1797 行） | 8 維度定義(與 schema.Dimension 同源) + 4 欄位 canon + **29 禁詞** + **10 情緒詞** + 類目模板 + 好壞範例 | 禁詞/情緒詞/維度供 `machine_checks` 與 `codex` 共用 |
| `machine_checks.py` | 重整自 `ai_writer_mvp/run/backend/writer_judge.py` 的 `run_machine_checks`（零 LLM 規則層） | 欄位無關的確定性 primitives：`forbidden_term_hits`(GT 豁免) / `emotional_term_hits` / `measure_name_length`(CJK·latin) / `name_length_severity` / `promo_bracket` / `has_section_structure` / `check_field` | 接 `arbiter` 第一道零 LLM 閘門 |

> **未沿用**：writer_judge.py 的 LLM 評分層（RUBRIC / FIELD_WEIGHTS / normalize_judge_result）——法官有自建 arbiter/diagnose。GEN-1/G1 舊版 prompt、合併式 70 行 writer prompt（已被取代）。

## 四、覆蓋限制

深度 prompt 只覆蓋 **行程流程 / 時效(限制) / 商品名稱** 三類；**費用 / 集合 / 成團 / SLA / 兌換** 無深度 prompt，
仍靠 `field_codex.json` 生成的淺 prompt。原 prompt 語境＝「孤立審查內容有無違規」，法官＝「對照客訴 + 客服 ground truth
判內容是否充分」→ 沿用時須把客訴 + ground_truth 當額外 context 餵入。

## 五、待接線（搬入≠生效）

1. `adequacy._real()` 改呼叫 `codex.get_field` + 對該面向注入 `judge_prompts/` 深度 prompt（取代硬編 system 字串）。
2. `arbiter.reconcile()` 接 `machine_checks.check_field` 第一道閘門 + `judge_rules.json` 的 verdict_hint。
3. 把 `judge_prompts` / `description.md` 防幻覺段拆進 `field_codex.json` 的 canon/deny/good/bad（單向同步，`data/parse_codex.py`）。

# C-1 Prompt Mock 評測實驗室 — Dev 交付報告

> 對應 PRD：[`PRD-C1-PROMPT-MOCK-EVAL.md`](./PRD-C1-PROMPT-MOCK-EVAL.md)｜日期：2026-07-13
> 狀態：**Phase 0–4 同步 MVP 完成**；已在真實 OpenAI API 上 full-scale 跑通 Layer 1+2（見 §2.5）；Phase 5（Batch，可選）未啟用。
> 硬約束遵守：未修改生產 `prejudge.py`／DB／前端；7 份 prompt 原樣導入未改。
> ⚠️ 本次實測為**同模型閉環 Mock**（Generator=Auditor=Judge=`gpt-5.5`）且資料集為 accept-all 未經人工複核 → 指標**偏樂觀，非真實準確率**（PRD §3/§8/§12）。

## 1. 交付物清單（PRD §21）

| # | 交付物 | 狀態 | 位置 |
|---|---|---|---|
| 1 | 7 份 baseline prompt + hash manifest | ✅ | `evals/prompt_lab/prompts/judges/`、`prompts/prompts_manifest.json` |
| 2 | Layer 1/2 plans（嚴格 130/210） | ✅ | `evals/prompt_lab/plans/` |
| 3 | Generator/Auditor prompt | ✅ | `evals/prompt_lab/prompts/generators/` |
| 4 | Pydantic 資料模型 + OpenAI gateway | ✅ | `scripts/prompt_lab/schemas.py`、`openai_gateway.py` |
| 5 | Generator/Auditor/人工審核/冻結 CLI | ✅ | `generate_cases.py`、`audit_cases.py`、`build_dataset.py` |
| 6 | C-1 Runner + 完整指標 | ✅ | `evaluate_prompt.py`、`metrics.py` |
| 7 | Markdown/JSON/CSV 報告 + Prompt diff | ✅ | `report.py`、`compare_runs.py` |
| 8 | 單元測試 + fake-client 集成測試 | ✅ | `backend/tests/prompt_lab/`（51 passed） |
| 9 | 使用 README | ✅ | `evals/prompt_lab/README.md`、`datasets/c1/README.md` |
| 10 | 5 條 live smoke 脫敏結果 | ✅ | §2.5（gen→audit→judge 真實鏈路跑通） |
| 11 | C-1 baseline Dev 報告 | ✅（同模型閉環·偏樂觀） | §2.5 + `tmp/prompt_lab/runs/c1-baseline-dev/summary.md` |
| 12 | 已知問題與下一步 | ✅ | 本文件 §5、§6 |

## 2. 測試結果

- **`pytest backend/tests/prompt_lab`：51 passed**（隔離 venv `.venv-promptlab`，零 API）。
  覆蓋 PRD §19 全清單：四類 Schema、Markdown 解析與占位符、證據子串、true/false/uncertain 分母、
  L2 exact/extra/missing、pair 不跨 split、切分可複現、重複 id 拒絕、resume、429/5xx retry、
  Schema error≠棄權、報告 fixture、dry-run 零 API。
- **指標精確性**：40 項手算 fixture 斷言全數吻合（域二分類 P/R/Spec/F1/FPR/FNR、L2 exact/over/under、
  證據 grounding、穩定性 flip/agreement、uncertain abstain/forced、contrast both-correct）。
- **全鏈 smoke（fake client，零 API）**：generate → audit → freeze → evaluate → report → compare 全通。
  - 生成：dry-run 零 API、evidence 100% 逐字落地、NFKC exact 去重、resume 跳過完成格。
  - 冻結：pair 不跨 split、case_id/exact-text/normalized-text/pair 四項無泄漏、manifest SHA-256 與檔案一致。
  - 評測：8 份報告齊出、traceability 100%、resume 只跳成功項。
  - 對比：fixed/regressed/unchanged_wrong + slice delta 正確。
- **Lint**：`ruff check` 全通、`ruff format` 已套（對齊 `backend/pyproject.toml [tool.ruff]`）。

## 2.5 本次 live 實測（2026-07-13，真實 OpenAI API）

模型演進：初試 `gpt-5.4-mini`（生成太套路）→ 使用者定案**全部 `gpt-5.5-2026-04-23`**。全程 0 失敗、evidence 100% 逐字。

**資料品質：`gpt-5.5` 遠優於 `gpt-5.4-mini`**（Layer 1 審核信號對比）：

| 信號 | 5.4-mini | 5.5 |
|---|--:|--:|
| review_required | 62 | 2 |
| near_duplicate | 51 | **0** |
| 負例暗含 C-1 | 4 | 0 |
| 域標籤分歧 | 1 | 0 |

**規模與判決**（`gpt-5.5`）：Layer 1 生成 130→冻結 128（89 dev/39 holdout）；Layer 2 生成 210（63 對照對成對完整）。
baseline judge：Layer 1 dev 89×3=267 runs；Layer 2 探索 dev 146×2=292 runs。合計 ~560 judge runs，0 失敗。

**指標（⚠️ 同模型閉環 + accept-all 未人審 → 偏樂觀）**：

| 面向 | Layer 1 dev | Layer 2 探索 dev |
|---|--:|--:|
| 域 P/R/Specificity/F1 | 1.0 全數 | 1.0 全數 |
| 對照對 Both-Correct | — | **1.0**（42 對）|
| 各邊界 FPR（14 個）| — | **全 0.0** |
| L2 Exact / Over-attr | 1.0 / 0.0 | 0.97 / **0.03** |
| **uncertain 被迫歸因率** | — | **0.275**（硬塞 C-1-3×5 等）|
| 證據 grounding | 1.0 | 1.0 |
| 穩定性 DomainFullAgreement | 1.0 | 0.993（1 flip）|

**唯一露馅點**：uncertain **被迫歸因 27.5%**（判官對模糊輸入過度自信、硬歸 C-1 而非棄權，命中 §17.3）＋ L2 **輕微過度歸因 3%**（§17.2）。其餘全 1.0 係閉環虛高，非真實力。

> 冻結資料集為 accept-all 探索版（未經人工複核）→ **刻意不入 Git**（PRD 要求人審後才算正式冻結）；本節數字僅供機器驗證與 v2 靶向，非可上線 baseline。

## 3. 工程硬門檻（PRD §12）現況

| 門檻 | 狀態 |
|---|---|
| FrozenCase Schema 合法率 100% | ✅ 冻結時 Pydantic 全驗；違則 fail-loud |
| 非空歸因證據逐字落地率 100% | ✅ runner 記 `evidence_grounded`；`report` 以 `grounding_quote_rate` 設門檻 |
| Prompt/資料/模型/參數可追溯 100% | ✅ 每 run 存 prompt_sha256+model+request_id；manifest 記 hash |
| Dev/Holdout 無 id/exact text/pair 泄漏 | ✅ `assert_no_leak` fail-loud |
| dry-run 零外部 API | ✅ 測試 `test_dry_run_zero_api` 以「呼叫即斷言失敗」的 fake 保證 |

Layer 1/2 準確度門檻（§12）已編碼於 `report.evaluate_gates`，待真實 baseline run 才有數值可判。

## 4. 待執行的 live 步驟（需 OPENAI_API_KEY，本 session 無法脫敏執行）

生成/審核/baseline 皆需真實 OpenAI 呼叫；完整 130/210 另需人工複核（contrast pair 與 uncertain 全審、
其餘自動通過 20% 抽審）。**5 條 live smoke** 與 **baseline Dev 報告**因此待金鑰與模型齊備後由下列指令產出：

```bash
export OPENAI_API_KEY=...  PROMPT_LAB_GENERATOR_MODEL=...  PROMPT_LAB_AUDITOR_MODEL=...  PROMPT_LAB_JUDGE_MODEL=...

# 5 條 live smoke（先小量驗真實鏈路）
.venv-promptlab/bin/python scripts/prompt_lab/generate_cases.py --plan evals/prompt_lab/plans/c1_layer1_plan.json \
  --model "$PROMPT_LAB_GENERATOR_MODEL" --out tmp/prompt_lab/smoke5.jsonl --limit 2   # ≤5 條
.venv-promptlab/bin/python scripts/prompt_lab/audit_cases.py --input tmp/prompt_lab/smoke5.jsonl \
  --model "$PROMPT_LAB_AUDITOR_MODEL" --out tmp/prompt_lab/smoke5-audit.jsonl --review-queue tmp/prompt_lab/smoke5-review.csv

# 之後：全量生成(--all --confirm-cost) → 人工複核 → 冻結 c1-v1 → baseline
.venv-promptlab/bin/python scripts/prompt_lab/evaluate_prompt.py \
  --prompt evals/prompt_lab/prompts/judges/01_C-1_content.md \
  --dataset evals/prompt_lab/datasets/c1/c1-v1-dev.jsonl --model "$PROMPT_LAB_JUDGE_MODEL" \
  --repeats 3 --out tmp/prompt_lab/runs/c1-baseline-dev --no-cache --all --confirm-cost
# → summary.md 即 baseline Dev 報告（含 §12 門檻判定）
```

> 選型參考（以帳號可用性與實測為準）：Generator 與 Judge 不同 snapshot；Auditor 另一模型（PRD §8）。

## 5. 已知 Prompt 風險（PRD §17）— baseline 要專門觀察的「已知問題」

**編碼者不得先修 prompt，必須先用資料跑 baseline。** 三個已知風險，Layer 1/2 已設計對應樣本暴露：

1. **`❌` 語義衝突**：C-1 prompt 的 `<domain_boundary>` 用 `❌` 表「不屬本域應棄權」，但 `<facet_catalog>`
   的 `❌誤判例`（如「未標示或模糊描述」）其實是**應命中** C-1 的違規寫法。Layer 1 正例（每 L2 10 條）
   專測模型是否被這層語義衝突誤導成棄權。
2. **多問題指令衝突**：prompt 同時要求「取最核心問題」與「列出所有明確問題，最多 2 條」。Layer 2 混合樣本
   （每 L2 4 條）會暴露真實行為（取一條 vs 列多條）。
3. **外部證據限制**：judge 看不到商品頁/訂單，只能判斷評論是否明確指稱頁面問題。Layer 2 不確定樣本
   （每 L2 4 條）測試模型面對「需查真實頁面才能判」時是否正確棄權（uncertain），而非硬歸因。

修 prompt 屬候選 v2 工作：baseline 數據出來後，用 `compare_runs.py` 驗證候選在目標邊界改善、
Layer 1 核心不下降 >1pp、Holdout 達標（PRD §12 晉級條件）。

## 6. 下一步（PRD §23）

1. 用 baseline 報告設計 C-1 prompt v2（針對 §5 三風險）。
2. C-1 Layer 1/2 達標後，引入小批真實匿名 Gold，量測 Mock 與真實落差。
3. 抽象 domain plan，擴展到 C-2～C-6。
4. Phase 5（可選）：Responses Batch 全量/多模型評測（gateway 已備 `batch_submit/poll/download` 介面）。

> **不得跳過真實 Gold 就用 Mock 分數作為上線依據。**

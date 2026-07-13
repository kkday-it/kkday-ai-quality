# C-1 Prompt Mock 評測實驗室（Prompt Lab）

離線評測 `01_C-1_content.md`（商品內容單域判官）的隔離工具。**不修改生產 prejudge 鏈路、資料庫或前端**（PRD §3）。
完整規格見 [`docs/PRD-C1-PROMPT-MOCK-EVAL.md`](../../docs/PRD-C1-PROMPT-MOCK-EVAL.md)。

## 它回答什麼

對一則已知含負向問題的評論，驗證 C-1 判官能否穩定：① 判定是否屬 C-1；② 命中時選對 L2 code；
③ 給出原文逐字證據；④ 不屬本域時回空歸因；⑤ 面對 C-2～C-6 近鄰、混合、對抗表達守住責任邊界。

> Mock 分數**不是**真實線上準確率；上線前必須用真實 Gold 重新定阈值（PRD §12）。

## 目錄

```
evals/prompt_lab/
  prompts/judges/00_polarity.md … 06_C-6_customer.md   # 7 份使用者提供 prompt，原樣導入（勿改）
  prompts/generators/c1_generator.md  c1_auditor.md     # 生成/審核 prompt（獨立於被測 judge）
  prompts/prompts_manifest.json                          # 全 prompt SHA-256（追溯 + 防竄改）
  plans/c1_layer1_plan.json  c1_layer2_plan.json         # 生成計畫（嚴格 130 / 210）
  datasets/c1/                                           # 冻結資料集 + manifest（見該目錄 README）
scripts/prompt_lab/
  schemas.py prompt_parser.py openai_gateway.py fake_client.py common.py
  build_plans.py build_manifest.py                       # 計畫/manifest 建構器（純函式）
  generate_cases.py audit_cases.py build_dataset.py      # 生成 → 審核 → 冻結
  evaluate_prompt.py metrics.py report.py compare_runs.py# 評測 → 指標 → 報告 → 對比
backend/tests/prompt_lab/                                # pytest（隔離 venv 執行）
```

中間產物一律落 `tmp/prompt_lab/`（gitignored）；冻結資料、計畫、baseline prompt 入 Git。

## 環境（隔離 venv）

Prompt Lab 用 **Responses API + strict Structured Outputs**，與生產 Chat Completions gateway 分離，故用獨立 venv（不動 `backend/.venv`）：

```bash
python3.12 -m venv .venv-promptlab
.venv-promptlab/bin/pip install "openai>=1.60" "pydantic>=2.9" jsonschema pytest
```

模型與金鑰（CLI 優先於 env；金鑰只從 env 讀，不記錄）：

```bash
export OPENAI_API_KEY=...            # 真打才需要；缺 key 時只能跑 --dry-run 與 fake-client 測試
export PROMPT_LAB_GENERATOR_MODEL=... PROMPT_LAB_AUDITOR_MODEL=... PROMPT_LAB_JUDGE_MODEL=...
```

Generator 與 Judge 預設不得用同一 snapshot；Auditor 建議另一模型（PRD §8）。

## 工作流

```
plan → 生成候選 → 獨立審核 → 人工複核佇列 → 冻結 Dev/Holdout → 跑 baseline → 指標+逐條錯誤 → 換候選 prompt → baseline vs candidate diff
```

```bash
# 0. 計畫與 manifest（純函式，零 API；已入庫，改規格才需重跑）
.venv-promptlab/bin/python scripts/prompt_lab/build_plans.py
.venv-promptlab/bin/python scripts/prompt_lab/build_manifest.py

# 1. 生成（先 dry-run 看請求數；預設 limit=5，全量需 --all + --confirm-cost）
.venv-promptlab/bin/python scripts/prompt_lab/generate_cases.py \
  --plan evals/prompt_lab/plans/c1_layer1_plan.json \
  --model "$PROMPT_LAB_GENERATOR_MODEL" \
  --out tmp/prompt_lab/c1-layer1-candidates.jsonl --workers 4 --resume --dry-run

# 2. 審核 + 產人工複核佇列
.venv-promptlab/bin/python scripts/prompt_lab/audit_cases.py \
  --input tmp/prompt_lab/c1-layer1-candidates.jsonl --model "$PROMPT_LAB_AUDITOR_MODEL" \
  --out tmp/prompt_lab/c1-layer1-audits.jsonl \
  --review-queue tmp/prompt_lab/c1-layer1-review.csv --resume

# 3.（人工在 review.csv 填 decision=accept|edit|reject）→ 冻結
.venv-promptlab/bin/python scripts/prompt_lab/build_dataset.py \
  --candidates tmp/prompt_lab/c1-layer1-candidates.jsonl \
  --audits tmp/prompt_lab/c1-layer1-audits.jsonl \
  --human-review tmp/prompt_lab/c1-layer1-review.csv \
  --dataset-version c1-v1 --out-dir evals/prompt_lab/datasets/c1 --split-seed 42

# 4. 跑 baseline（repeats=3，真打、不快取；--no-cache 為契約旗標）
.venv-promptlab/bin/python scripts/prompt_lab/evaluate_prompt.py \
  --prompt evals/prompt_lab/prompts/judges/01_C-1_content.md \
  --dataset evals/prompt_lab/datasets/c1/c1-v1-dev.jsonl \
  --model "$PROMPT_LAB_JUDGE_MODEL" --repeats 3 \
  --out tmp/prompt_lab/runs/c1-baseline-dev --workers 8 --no-cache --resume

# 5. 換候選 prompt 再跑一次（不覆蓋 baseline），再對比
.venv-promptlab/bin/python scripts/prompt_lab/compare_runs.py \
  --baseline tmp/prompt_lab/runs/c1-baseline-dev \
  --candidate tmp/prompt_lab/runs/c1-v2-dev \
  --out tmp/prompt_lab/comparisons/c1-baseline-vs-v2
```

成本護欄：預設 `--limit 5`；`--dry-run` 印請求數且零 API；全量需 `--all`（真打再加 `--confirm-cost`）。

## 每次評測輸出（`--out` 目錄，PRD §13）

`run_manifest.json`、`raw_results.jsonl`、`metrics.json`（含 §12 門檻判定）、`summary.md`、
`errors.csv`、`unstable_cases.csv`、`boundary_matrix.csv`、`contrast_pairs.csv`。

## 測試（fake client，零 API）

```bash
.venv-promptlab/bin/python -m pytest backend/tests/prompt_lab
```

涵蓋：四類 Schema、Markdown 解析與占位符、證據子串、true/false/uncertain 分母、L2 exact/extra/missing、
pair 不跨 split、切分可複現、重複 id 拒絕、resume、429/5xx retry、Schema error≠棄權、報告 fixture、dry-run 零 API。

## 已知 prompt 風險（先基線、後修改；PRD §17）

編碼者**不得先改 prompt**，必須先用資料跑 baseline。三個已知風險：

1. **`❌` 語義衝突**：`<domain_boundary>` 的 `❌`＝「不屬本域應棄權」，但 `<facet_catalog>` 的 `❌誤判例`
   常是**應觸發** C-1 的違規寫法（如「未標示或模糊描述」）。Layer 1 專測此點。
2. **多問題指令衝突**：prompt 同時寫「取最核心問題」與「列出所有明確問題，最多 2 條」。Layer 2 混合樣本暴露真實行為。
3. **外部證據限制**：judge 看不到商品頁與訂單，只能判斷評論是否明確指稱頁面問題；依賴外部資料者一律標 `uncertain`。

## 隔離不變式

- 不 import `backend.app`，不碰生產判決/DB/前端。
- Generator / Auditor / Judge 各用獨立 prompt；Generator 不看 Judge 輸出；Auditor 不用被測 C-1 prompt。
- 被測 judge 真打、禁用生產 exact-match cache、repeats 各存不做多數投票。

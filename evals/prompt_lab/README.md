# C-1 / C-2 Prompt Mock 評測實驗室（Prompt Lab）

离线生成与评测 `01_C-1_content.md`、`02_C-2_quality.md` 单域判官的隔离工具。**不修改生产 prejudge 链路、数据库或前端**（PRD §3）。
完整規格見 [`docs/PRD-C1-PROMPT-MOCK-EVAL.md`](../../docs/PRD-C1-PROMPT-MOCK-EVAL.md)。

## 它回答什麼

對一則已知含負向問題的評論，驗證 C-1 判官能否穩定：① 判定是否屬 C-1；② 命中時選對 L2 code；
③ 給出原文逐字證據；④ 不屬本域時回空歸因；⑤ 面對 C-2～C-6 近鄰、混合、對抗表達守住責任邊界。

> Mock 分數**不是**真實線上準確率；上線前必須用真實 Gold 重新定阈值（PRD §12）。

## 目錄

```
evals/prompt_lab/
  prompts/judges/00_polarity.md … 06_C-6_customer.md   # 7 份使用者提供 prompt，原樣導入（勿改）
  prompts/generators/c{1,2}_generator.md / c{1,2}_auditor.md # 各域独立生成/审核 prompt
  prompts/prompts_manifest.json                          # 全 prompt SHA-256（追溯 + 防竄改）
  plans/c1_layer{1,2}_plan.json  c2_layer{1,2}_plan.json  # 各域覆盖计划
  datasets/c1/                                           # 冻結資料集 + manifest（見該目錄 README）
scripts/prompt_lab/
  schemas.py prompt_parser.py openai_gateway.py fake_client.py common.py
  build_plans.py build_c2_plans.py build_manifest.py     # 計畫/manifest 建構器（純函式）
  generate_cases.py audit_cases.py build_dataset.py      # 生成 → 審核 → 冻結
  evaluate_prompt.py metrics.py report.py compare_runs.py# 評測 → 指標 → 報告 → 對比
backend/tests/prompt_lab/                                # pytest（隔離 venv 執行）
```

中間產物一律落 `tmp/prompt_lab/`（gitignored）；冻結資料、計畫、baseline prompt 入 Git。

## 環境（隔離 venv）

Prompt Lab 的 OpenAI 角色使用 **Responses API + strict Structured Outputs**；Gemini Generator 使用官方
OpenAI compatibility endpoint 的 Chat Completions + JSON Schema。两者都与生产 gateway 分离，故用独立 venv（不动 `backend/.venv`）：

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

### Gemini 3.5 独立出题模型

使用 Gemini 3.5 Flash 生成题目，保留 OpenAI 模型负责 Auditor/Judge：

```bash
export GEMINI_API_KEY=...
export PROMPT_LAB_GENERATOR_PROVIDER=gemini
export PROMPT_LAB_GENERATOR_MODEL=gemini-3.5-flash
export PROMPT_LAB_AUDITOR_MODEL=gpt-5.5-2026-04-23
export PROMPT_LAB_JUDGE_MODEL=gpt-5.5-2026-04-23
```

`--provider auto`（默认）会将 `gemini-*` 模型自动路由到 Gemini API；也可显式传
`--provider gemini --model gemini-3.5-flash`。Generator 仍使用原本的 plan、C2 Generator prompt、
JSON Schema、逐字证据校验与成本护栏，只有模型供应商改变。

批量生成 C2 数据可直接使用封装脚本。默认一轮为 114 次 Gemini 调用、目标 260 条；支持断点续跑：

```bash
# 零 API 检查
DRY_RUN=1 bash scripts/prompt_lab/generate_c2_gemini_mock.sh

# 1 轮，目标 260 条
CONFIRM_COST=1 bash scripts/prompt_lab/generate_c2_gemini_mock.sh

# 5 轮，目标 1,300 条；跨轮添加 case_id 前缀并去重
CONFIRM_COST=1 ROUNDS=5 WORKERS=4 \
  OUT_DIR=tmp/prompt_lab/c2-gemini35-1300 \
  bash scripts/prompt_lab/generate_c2_gemini_mock.sh
```

## 工作流

```
plan → 生成候選 → 獨立審核 → 人工複核佇列 → 冻結 Dev/Holdout → 跑 baseline → 指標+逐條錯誤 → 換候選 prompt → baseline vs candidate diff
```

```bash
# 0. 計畫與 manifest（純函式，零 API；已入庫，改規格才需重跑）
.venv-promptlab/bin/python scripts/prompt_lab/build_plans.py
.venv-promptlab/bin/python scripts/prompt_lab/build_c2_plans.py
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

### C-2 批次生成

`generate_cases.py` 会读取 plan 的 `domain_under_test`，自动加载对应 Generator prompt 和 L2 schema；模型仍由独立的
`PROMPT_LAB_GENERATOR_MODEL` 或 `--model` 指定。无需复制脚本或把 C2 judge prompt 交给生成模型。

```bash
# 先看请求规模（54 个生成格，目标 110 条；零 API）
.venv-promptlab/bin/python scripts/prompt_lab/generate_cases.py \
  --plan evals/prompt_lab/plans/c2_layer1_plan.json \
  --out tmp/prompt_lab/c2-layer1-candidates.jsonl --dry-run

# Smoke：只跑前 2 格；正常应得到 5 条（3 + 2）
.venv-promptlab/bin/python scripts/prompt_lab/generate_cases.py \
  --plan evals/prompt_lab/plans/c2_layer1_plan.json \
  --provider gemini --model gemini-3.5-flash \
  --out tmp/prompt_lab/c2-smoke5.jsonl --limit 2

# 全量：显式确认成本，可断点续跑
.venv-promptlab/bin/python scripts/prompt_lab/generate_cases.py \
  --plan evals/prompt_lab/plans/c2_layer1_plan.json \
  --out tmp/prompt_lab/c2-layer1-candidates.jsonl \
  --workers 4 --resume --all --confirm-cost

# C-2 Auditor 同样依输入数据自动路由
.venv-promptlab/bin/python scripts/prompt_lab/audit_cases.py \
  --input tmp/prompt_lab/c2-layer1-candidates.jsonl \
  --out tmp/prompt_lab/c2-layer1-audits.jsonl \
  --review-queue tmp/prompt_lab/c2-layer1-review.csv --resume
```

C-2 Layer 2 为 60 个生成格、目标 150 条：最小对照 90、混合 20、存疑 20、对抗 20。
以上命令会自动读取 `evals/prompt_lab/.env`；临时覆盖时再加 `--model <独立模型 snapshot>`。

## 每次評測輸出（`--out` 目錄，PRD §13）

`run_manifest.json`、`raw_results.jsonl`、`metrics.json`（含 §12 門檻判定）、`summary.md`、
`errors.csv`、`unstable_cases.csv`、`boundary_matrix.csv`、`contrast_pairs.csv`。

## C3～C6 五轮 audited-candidate baseline

C3～C6 使用 `evals/prompt_lab/domains/*.json` 作为生成政策与覆盖矩阵的单一事实源。以下流程不会把 AI 合成候选冒充人工 Gold；所有 uncertain、domain pair、l2 pair、Auditor review_required、C3-5/C3-7 以及其余 accepted 的分层 20% 都会进入人工队列。

```bash
# 计划与 Prompt hash（零 API）
for d in 3 4 5 6; do
  .venv-promptlab/bin/python scripts/prompt_lab/build_domain_plans.py --domain "C-$d"
done
.venv-promptlab/bin/python scripts/prompt_lab/build_manifest.py

# 每域 dry-run（零 API）
DOMAIN=C-3 ROUNDS=5 DRY_RUN=1 \
  OUT_DIR=tmp/prompt_lab/c3-gemini35-5rounds \
  bash scripts/prompt_lab/generate_domain_gemini_mock.sh

# 五轮真实生成；C4/C5/C6 只替换 DOMAIN 与目录
CONFIRM_COST=1 DOMAIN=C-3 ROUNDS=5 WORKERS=4 \
  OUT_DIR=tmp/prompt_lab/c3-gemini35-5rounds \
  bash scripts/prompt_lab/generate_domain_gemini_mock.sh

# 全量独立 Auditor
.venv-promptlab/bin/python scripts/prompt_lab/audit_cases.py \
  --input tmp/prompt_lab/c3-gemini35-5rounds/c3-all-candidates.jsonl \
  --model gpt-5.5-2026-04-23 \
  --out tmp/prompt_lab/c3-gemini35-5rounds/c3-all-audits.jsonl \
  --review-queue tmp/prompt_lab/c3-gemini35-5rounds/c3-review.csv \
  --workers 8 --resume --all --confirm-cost

# 未修改 Judge Prompt 的 audited-candidate baseline
.venv-promptlab/bin/python scripts/prompt_lab/evaluate_prompt.py \
  --prompt evals/prompt_lab/prompts/judges/03_C-3_supplier.md \
  --dataset tmp/prompt_lab/c3-gemini35-5rounds/c3-all-candidates.jsonl \
  --model gpt-5.4-mini-2026-03-17 \
  --temperature 1 --reasoning-effort high --thinking --repeats 1 \
  --out tmp/prompt_lab/c3-gemini35-5rounds/judge-run-gpt54mini-high \
  --workers 8 --no-cache --resume --all --confirm-cost
```

四域计划每轮分别为 376／198／198／318 条，合计 1,090；五轮目标 5,450。批处理脚本支持 resume、每轮 ID 前缀、NFKC＋空白正规化去重、失败格重试、generation manifest、slice counts 与失败记录。

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

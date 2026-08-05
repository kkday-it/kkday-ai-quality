# C-1～C-6 Prompt Mock 評測實驗室（Prompt Lab）

离线生成与评测 `prompts/judges/` 六域单域判官（C-1～C-6）＋ `00_polarity` 的隔离工具。**不修改生产 prejudge 链路、数据库或前端**（PRD §3）。
完整規格見 [`docs/PRD-C1-PROMPT-MOCK-EVAL.md`](../../docs/PRD-C1-PROMPT-MOCK-EVAL.md)、[`docs/PRD-C3-C6-MOCK-DATA-WORKFLOW.md`](../../docs/PRD-C3-C6-MOCK-DATA-WORKFLOW.md)。

## 它回答什麼

對一則已知含負向問題的評論，驗證受測域判官能否穩定：① 判定是否屬本域；② 命中時選對 L2 code；
③ 給出原文逐字證據；④ 不屬本域時回空歸因；⑤ 面對其餘五域近鄰、混合、對抗表達守住責任邊界。

> Mock 分數**不是**真實線上準確率；上線前必須用真實 Gold 重新定阈值（PRD §12）。

## 目錄

```
evals/prompt_lab/
  .env.example                                           # 金鑰/模型設定範本（複製為 .env，已 gitignore）
  prompts/judges/                                        # 9 支：00_polarity + 01_C-1…06_C-6（7 支使用者提供、原樣導入勿改）
                                                         #   ＋ 01_C-1_content_v2 / 02_C-2_quality_v2 兩支候選
  prompts/generators/c{1..6}_generator.md / _auditor.md  # 12 支：六域各自獨立的生成/審核 prompt
  prompts/prompts_manifest.json                          # 全 21 支 prompt 的 SHA-256（追溯 + 防竄改）
  plans/c{1..6}_layer{1,2}_plan.json                     # 12 份覆盖计划（cells + total_target）
  domains/c{3..6}.json                                   # C3～C6 生成政策與覆蓋矩陣的單一事實源
  datasets/c1/                                           # 冻結資料集 + manifest（見該目錄 README）
scripts/prompt_lab/                                      # 24 個檔（模組本體，非本目錄）
  common.py schemas.py prompt_parser.py domains.py       # 共用：.env 載入 / 成本護欄 / Schema / prompt 解析 / 域政策
  openai_gateway.py gemini_gateway.py fake_client.py     # 供應商閘道（Responses / Chat+JSON Schema）與零 API 假 client
  build_plans.py build_c2_plans.py build_domain_plans.py # 計畫建構器（純函式）
  build_manifest.py                                      # prompt SHA-256 manifest
  generate_cases.py audit_cases.py build_dataset.py      # 生成 → 審核 → 冻結
  evaluate_prompt.py metrics.py report.py compare_runs.py# 評測 → 指標 → 報告 → 對比
  run_judge_debug_workbook.py build_c3_c6_summary.py     # 人工除錯工作簿跑判官 / C3–C6 彙總
  generate_c2_gemini_mock.sh generate_domain_gemini_mock.sh  # 批次生成封裝（多輪 + resume + 去重）
  build_domain_eval_workbook.mjs build_c3_c6_checkpoint_workbook.mjs  # 工作簿產出（Node）
backend/tests/prompt_lab/                                # pytest（零 API，fake client）
```

冻結資料、計畫、baseline prompt 入 Git；中間產物落 `data/prompt_lab/`（已 gitignore，且 host／容器雙向可見，見下節掛載事實 2）。

## 執行環境（容器內，不裝本機）

Prompt Lab 的 OpenAI 角色使用 **Responses API + strict Structured Outputs**；Gemini Generator 使用官方
OpenAI compatibility endpoint 的 Chat Completions + JSON Schema。隔離靠 code 邊界維持——腳本**零 import
`backend.app`**、不用生產 diskcache——而非另建直譯器，故直接用 backend 容器既有的 `openai` / `pydantic` /
`jsonschema` / `openpyxl` 執行即可，**不在本機裝 venv 或 pip 套件**（專案環境鐵律）。

`docker-compose.dev.yml` 已把 `./scripts` 掛到 `/app/scripts`、`./evals` 掛到 `/app/evals`（**唯讀**）。
容器預設工作目錄是 `/app/backend`，本文相對路徑一律以 `/app` 為基準，故指令都帶 `-w /app`。
以下三個 shorthand 在 repo 根執行一次，後續章節的指令直接沿用：

```bash
# 唯讀執行：generate / audit / evaluate / compare / pytest 皆走這條
pl()    { docker compose -f docker-compose.dev.yml exec -T -w /app backend python "$@"; }
# 需要 shell 展開（.sh 封裝腳本帶環境變數前綴）時走這條
pl_sh() { docker compose -f docker-compose.dev.yml exec -T -w /app backend sh -lc "$@"; }
# 需要寫回 evals/ 時走這條：一次性 rw 覆寫唯讀掛載
pl_rw() { docker run --rm -w /app -v "$PWD/evals:/app/evals" -v "$PWD/scripts:/app/scripts" \
            --entrypoint python kkday-ai-quality-backend:dev "$@"; }
```

⚠️ 三個掛載事實直接決定指令跑不跑得起來：

1. **`/app/evals` 是唯讀**：寫回 `evals/` 的建構器（`build_plans.py`／`build_c2_plans.py`／
   `build_domain_plans.py`／`build_manifest.py`，以及 `--out-dir` 指向 `evals/` 的 `build_dataset.py`）
   在 `exec` 下會 `OSError: Read-only file system`，必須改用 `pl_rw`。
2. **`/app/tmp` 不是 bind mount**：容器內可寫，但 host 看不到、容器重建即消失（連 `--resume` 斷點一起沒）。
   要在 host 取用產物就把 `--out` / `OUT_DIR` 指到 `data/prompt_lab/…`（`./data` 有掛載且已 gitignore）。
3. **`.sh` 封裝腳本預設 `PYTHON=.venv-promptlab/bin/python`** 且會檢查可執行，容器內須顯式覆寫
   `PYTHON=$(command -v python)`，否則直接 `⛔ 找不到可执行 Python`。

模型與金鑰：複製 `.env.example` 為 `evals/prompt_lab/.env`（已 gitignore；唯讀掛載不影響**讀取**），
CLI `--model` > 真實環境變數 > `.env`；金鑰只從環境讀，不寫進產物。

```bash
OPENAI_API_KEY=...            # 真打才需要；缺 key 時只能跑 --dry-run 與 fake-client 測試
PROMPT_LAB_GENERATOR_MODEL=... PROMPT_LAB_AUDITOR_MODEL=... PROMPT_LAB_JUDGE_MODEL=...
```

Generator 與 Judge 預設不得用同一 snapshot；Auditor 建議另一模型（PRD §8）。

### Gemini 独立出题模型

用 Gemini 生成题目、保留 OpenAI 模型负责 Auditor/Judge，借此打破「同模型出题、同模型评判」闭环。
在 `.env` 設：

```dotenv
GEMINI_API_KEY=...
PROMPT_LAB_GENERATOR_PROVIDER=gemini
PROMPT_LAB_GENERATOR_MODEL=gemini-3.5-flash
PROMPT_LAB_AUDITOR_MODEL=gpt-5.5-2026-04-23
PROMPT_LAB_JUDGE_MODEL=gpt-5.5-2026-04-23
```

`--provider auto`（默认）会将 `gemini-*` 模型自动路由到 Gemini API；也可显式传
`--provider gemini --model gemini-3.5-flash`。Generator 仍使用原本的 plan、Generator prompt、
JSON Schema、逐字证据校验与成本护栏，只有模型供应商改变。

批量生成 C2 数据可直接使用封装脚本。默认一轮为 114 次 Gemini 调用、目标 260 条；支持断点续跑：

```bash
# 零 API 检查
pl_sh 'PYTHON=$(command -v python) DRY_RUN=1 bash scripts/prompt_lab/generate_c2_gemini_mock.sh'

# 1 轮，目标 260 条
pl_sh 'PYTHON=$(command -v python) CONFIRM_COST=1 bash scripts/prompt_lab/generate_c2_gemini_mock.sh'

# 5 轮，目标 1,300 条；跨轮添加 case_id 前缀并去重
pl_sh 'PYTHON=$(command -v python) CONFIRM_COST=1 ROUNDS=5 WORKERS=4 \
  OUT_DIR=data/prompt_lab/c2-gemini35-1300 \
  bash scripts/prompt_lab/generate_c2_gemini_mock.sh'
```

## 工作流

```
plan → 生成候選 → 獨立審核 → 人工複核佇列 → 冻結 Dev/Holdout → 跑 baseline → 指標+逐條錯誤 → 換候選 prompt → baseline vs candidate diff
```

```bash
# 0. 計畫與 manifest（純函式，零 API；已入庫，改規格才需重跑。寫回 evals/ 故走 pl_rw）
pl_rw /app/scripts/prompt_lab/build_plans.py
pl_rw /app/scripts/prompt_lab/build_c2_plans.py
pl_rw /app/scripts/prompt_lab/build_manifest.py

# 1. 生成（先 dry-run 看請求數；預設 limit=5，全量需 --all + --confirm-cost）
pl scripts/prompt_lab/generate_cases.py \
  --plan evals/prompt_lab/plans/c1_layer1_plan.json \
  --model "$PROMPT_LAB_GENERATOR_MODEL" \
  --out data/prompt_lab/c1-layer1-candidates.jsonl --workers 4 --resume --dry-run

# 2. 審核 + 產人工複核佇列
pl scripts/prompt_lab/audit_cases.py \
  --input data/prompt_lab/c1-layer1-candidates.jsonl --model "$PROMPT_LAB_AUDITOR_MODEL" \
  --out data/prompt_lab/c1-layer1-audits.jsonl \
  --review-queue data/prompt_lab/c1-layer1-review.csv --resume

# 3.（人工在 review.csv 填 decision=accept|edit|reject）→ 冻結（寫進 evals/ 故走 pl_rw）
pl_rw /app/scripts/prompt_lab/build_dataset.py \
  --candidates /app/data/prompt_lab/c1-layer1-candidates.jsonl \
  --audits /app/data/prompt_lab/c1-layer1-audits.jsonl \
  --human-review /app/data/prompt_lab/c1-layer1-review.csv \
  --dataset-version c1-v1 --out-dir /app/evals/prompt_lab/datasets/c1 --split-seed 42

# 4. 跑 baseline（repeats=3，真打、不快取；--no-cache 為契約旗標）
pl scripts/prompt_lab/evaluate_prompt.py \
  --prompt evals/prompt_lab/prompts/judges/01_C-1_content.md \
  --dataset evals/prompt_lab/datasets/c1/c1-v1-dev.jsonl \
  --model "$PROMPT_LAB_JUDGE_MODEL" --repeats 3 \
  --out data/prompt_lab/runs/c1-baseline-dev --workers 8 --no-cache --resume

# 5. 換候選 prompt 再跑一次（不覆蓋 baseline），再對比
pl scripts/prompt_lab/compare_runs.py \
  --baseline data/prompt_lab/runs/c1-baseline-dev \
  --candidate data/prompt_lab/runs/c1-v2-dev \
  --out data/prompt_lab/comparisons/c1-baseline-vs-v2
```

成本護欄：`generate_cases` / `audit_cases` 預設 `--limit 5`，`evaluate_prompt` 的下限為 `max(5, repeats)`；
`--dry-run` 印請求數且零 API；超過上限需 `--all`（真打再加 `--confirm-cost`）。

### C-2 批次生成

`generate_cases.py` 会读取 plan 的 `domain_under_test`，自动加载对应 Generator prompt 和 L2 schema；模型仍由独立的
`PROMPT_LAB_GENERATOR_MODEL` 或 `--model` 指定。无需复制脚本或把 judge prompt 交给生成模型。

```bash
# 先看请求规模（54 个生成格，目标 110 条；零 API）
pl scripts/prompt_lab/generate_cases.py \
  --plan evals/prompt_lab/plans/c2_layer1_plan.json \
  --out data/prompt_lab/c2-layer1-candidates.jsonl --dry-run

# Smoke：只跑前 2 格；正常应得到 5 条（3 + 2）
pl scripts/prompt_lab/generate_cases.py \
  --plan evals/prompt_lab/plans/c2_layer1_plan.json \
  --provider gemini --model gemini-3.5-flash \
  --out data/prompt_lab/c2-smoke5.jsonl --limit 2

# 全量：显式确认成本，可断点续跑
pl scripts/prompt_lab/generate_cases.py \
  --plan evals/prompt_lab/plans/c2_layer1_plan.json \
  --out data/prompt_lab/c2-layer1-candidates.jsonl \
  --workers 4 --resume --all --confirm-cost

# C-2 Auditor 同样依输入数据自动路由
pl scripts/prompt_lab/audit_cases.py \
  --input data/prompt_lab/c2-layer1-candidates.jsonl \
  --out data/prompt_lab/c2-layer1-audits.jsonl \
  --review-queue data/prompt_lab/c2-layer1-review.csv --resume
```

C-2 Layer 2 为 60 个生成格、目标 150 条。以上命令会自动读取 `evals/prompt_lab/.env`；临时覆盖时再加 `--model <独立模型 snapshot>`。

## 每次評測輸出（`--out` 目錄，PRD §13）

`run_manifest.json`、`raw_results.jsonl`、`metrics.json`（含 §12 門檻判定）、`summary.md`、
`errors.csv`、`unstable_cases.csv`、`boundary_matrix.csv`、`contrast_pairs.csv`。

## C3～C6 五轮 audited-candidate baseline

C3～C6 使用 `evals/prompt_lab/domains/*.json` 作为生成政策与覆盖矩阵的单一事实源。以下流程不会把 AI 合成候选冒充人工 Gold；所有 uncertain、domain pair、l2 pair、Auditor review_required、C3-5/C3-7 以及其余 accepted 的分层 20% 都会进入人工队列。

```bash
# 计划与 Prompt hash（零 API；写回 evals/ 故走 pl_rw）
for d in 3 4 5 6; do
  pl_rw /app/scripts/prompt_lab/build_domain_plans.py --domain "C-$d"
done
pl_rw /app/scripts/prompt_lab/build_manifest.py

# 每域 dry-run（零 API）
pl_sh 'PYTHON=$(command -v python) DOMAIN=C-3 ROUNDS=5 DRY_RUN=1 \
  OUT_DIR=data/prompt_lab/c3-gemini35-5rounds \
  bash scripts/prompt_lab/generate_domain_gemini_mock.sh'

# 五轮真实生成；C4/C5/C6 只替换 DOMAIN 与目录
pl_sh 'PYTHON=$(command -v python) CONFIRM_COST=1 DOMAIN=C-3 ROUNDS=5 WORKERS=4 \
  OUT_DIR=data/prompt_lab/c3-gemini35-5rounds \
  bash scripts/prompt_lab/generate_domain_gemini_mock.sh'

# 全量独立 Auditor
pl scripts/prompt_lab/audit_cases.py \
  --input data/prompt_lab/c3-gemini35-5rounds/c3-all-candidates.jsonl \
  --model gpt-5.5-2026-04-23 \
  --out data/prompt_lab/c3-gemini35-5rounds/c3-all-audits.jsonl \
  --review-queue data/prompt_lab/c3-gemini35-5rounds/c3-review.csv \
  --workers 8 --resume --all --confirm-cost

# 未修改 Judge Prompt 的 audited-candidate baseline
pl scripts/prompt_lab/evaluate_prompt.py \
  --prompt evals/prompt_lab/prompts/judges/03_C-3_supplier.md \
  --dataset data/prompt_lab/c3-gemini35-5rounds/c3-all-candidates.jsonl \
  --model gpt-5.4-mini-2026-03-17 \
  --temperature 1 --reasoning-effort high --thinking --repeats 1 \
  --out data/prompt_lab/c3-gemini35-5rounds/judge-run-gpt54mini-high \
  --workers 8 --no-cache --resume --all --confirm-cost
```

四域计划每轮分别为 376／198／198／318 条，合计 1,090；五轮目标 5,450。批处理脚本支持 resume、每轮 ID 前缀、NFKC＋空白正规化去重、失败格重试、generation manifest、slice counts 与失败记录。

## 測試（fake client，零 API）

```bash
docker compose -f docker-compose.dev.yml exec -T -w /app backend python -m pytest backend/tests/prompt_lab
```

73 個測試，涵蓋：四類 Schema、Markdown 解析與占位符、證據子串、true/false/uncertain 分母、L2 exact/extra/missing、
pair 不跨 split、切分可複現、重複 id 拒絕、resume、429/5xx retry、Schema error≠棄權、報告 fixture、dry-run 零 API、
Gemini 供應商路由與非 JSON 回應處理、C3～C6 四域 plan/schema 與生成審核路由。

## 已知 prompt 風險（先基線、後修改；PRD §17）

編碼者**不得先改 prompt**，必須先用資料跑 baseline。三個已知風險：

1. **`❌` 語義衝突**：`<domain_boundary>` 的 `❌`＝「不屬本域應棄權」，但 `<facet_catalog>` 的 `❌誤判例`
   常是**應觸發**本域的違規寫法（如「未標示或模糊描述」）。Layer 1 專測此點。
2. **多問題指令衝突**：prompt 同時寫「取最核心問題」與「列出所有明確問題，最多 2 條」。Layer 2 混合樣本暴露真實行為。
3. **外部證據限制**：judge 看不到商品頁與訂單，只能判斷評論是否明確指稱頁面問題；依賴外部資料者一律標 `uncertain`。

## 隔離不變式

- 不 import `backend.app`，不碰生產判決/DB/前端。
- Generator / Auditor / Judge 各用獨立 prompt；Generator 不看 Judge 輸出；Auditor 不用被測 judge prompt。
- 被測 judge 真打、禁用生產 exact-match cache、repeats 各存不做多數投票。

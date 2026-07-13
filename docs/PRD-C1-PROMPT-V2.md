# 任务 Prompt：C-1 判官 Prompt v2 设计与回归验证

> 交给编码 AI 直接执行。上一阶段（Prompt Lab Phase 0–4 + baseline 实测）已完成，见
> [`docs/C1-PROMPT-LAB-DEV-REPORT.md`](./C1-PROMPT-LAB-DEV-REPORT.md) 与 [`docs/PRD-C1-PROMPT-MOCK-EVAL.md`](./PRD-C1-PROMPT-MOCK-EVAL.md)。
> 本任务只做「设计候选 prompt v2 + 用现成工具回归对比」，**不重建工具、不碰生产**。

## 0. 前置状态（已存在，先读懂再动手）

- 隔离评测实验室已建好：`scripts/prompt_lab/`（schemas / gateway / parser / generate / audit / build_dataset / evaluate / metrics / report / compare_runs）+ `backend/tests/prompt_lab/`（51 tests，`.venv-promptlab` 跑）。
- baseline prompt：`evals/prompt_lab/prompts/judges/01_C-1_content.md`（**冻结基线，禁止修改**）。
- 运行环境：`.venv-promptlab`（Python 3.12，openai/pydantic/jsonschema/pytest）；模型/金鑰从 `evals/prompt_lab/.env` 读（`OPENAI_API_KEY` 由人填、勿写进任何文件、勿提交）。
- **baseline 实测结论（关键，务必内化）**：在同模型闭环 Mock（Generator=Auditor=Judge=`gpt-5.5`）+ accept-all 未人审数据上，域/边界指标全 1.0（**虚高，非真实力**）；**唯一真实弱点**：
  - **§17.3 弃权失败**：uncertain 样本约 **27.5% 被判官硬归成 C-1**（而非棄权），硬塞集中在 C-1-3/C-1-1/C-1-4/C-1-6。
  - **§17.2 多问题指令冲突**：L2 有 ~3% 过度归因（prompt 同时写「取最核心」与「列出所有，最多 2 条」）。
  - **§17.1 `❌` 语义冲突**：`<domain_boundary>` 的 `❌`＝「应弃权」，但 `<facet_catalog>` 的 `❌误判例`（如「未标示或模糊描述」）其实是**应命中 C-1** 的违规写法——同一符号两义，易误导。

## 1. 目标

产出候选 `01_C-1_content_v2.md`，在**不损害**既有强项（域命中、边界区分、证据落地）的前提下，修复上述三个风险，**主攻弃权**：让判官面对「光看文本无法判定是页面写错还是现场偏离/旅客没看」的输入时**正确回空（弃权）**，而非硬归 C-1。然后用 `compare_runs.py` 量化 v2 vs baseline 的 fixed/regressed。

## 2. 硬约束（违反即失败）

1. **禁止修改** `01_C-1_content.md`（基线）、禁止改 `scripts/prompt_lab/` 引擎逻辑与 `backend/app/`（生产 prejudge/DB/前端）。v2 是**新文件**。
2. v2 必须保持**可被 `prompt_parser.py` 解析**：`## System` / `## User`（含 `{POLARITY}` `{TEXT}`）/ `## Schema`（合法 strict JSON Schema，`l2_code` enum 仍为 C-1-1..C-1-7，maxItems 2）。改 schema 会破坏 runner。
3. **禁止用被测判官反向筛数据**；**禁止把 Mock 分数当真实准确率**。
4. 新增/重生成任何数据，contrast pair 与 uncertain **必须人工复核**后才可冻结（`build_dataset` 已强制）；金鑰只走 env。
5. 成本护栏沿用：`--dry-run` 先报数，全量才 `--all --confirm-cost`。
6. **不得为了刷 Mock 分数过拟合**：v2 的改动必须是「判定契约本身更清晰/正确」，而非针对某几条 Mock 文本打补丁。

## 3. 具体步骤

### 3.1 设计 v2（改 prompt 文案，非改 schema）
基于 baseline 文本改写，针对三风险：
- **弃权（重点）**：把 `<abstain_rules>` 强化为「可操作判据」——明确列出「需要查看真实页面/订单才能判定」「无法从文本区分页面写错 vs 现场偏离 vs 旅客没看」等情形一律回空；给 2–3 个 uncertain 正反示例（棄权 vs 命中的对照）。
- **多问题契约（§17.2）**：二选一并统一——建议「列出所有明确成立的本域问题，最多 2 条；无则空」，删除与之冲突的「取最核心」表述（或反之，但只能留一套）。
- **`❌` 语义（§17.1）**：把 `<facet_catalog>` 的 `❌误判例` 改为无歧义标注，例如 `✅合规写法 / 🚩违规写法(应命中本面向)`，与 `<domain_boundary>` 的「弃权」语义脱钩。

### 3.2 评测 v2（对同一数据集，不覆盖 baseline run）
```bash
# 前置：需有一份「人工复核过并冻结」的数据集（见 §4 前置条件）
.venv-promptlab/bin/python scripts/prompt_lab/evaluate_prompt.py \
  --prompt evals/prompt_lab/prompts/judges/01_C-1_content_v2.md \
  --dataset <冻结 dev.jsonl> --repeats 3 \
  --out tmp/prompt_lab/runs/c1-v2-dev --all --confirm-cost
```

### 3.3 对比 + 晋级判定（PRD §12）
```bash
.venv-promptlab/bin/python scripts/prompt_lab/compare_runs.py \
  --baseline tmp/prompt_lab/runs/c1-baseline-dev \
  --candidate tmp/prompt_lab/runs/c1-v2-dev \
  --out tmp/prompt_lab/comparisons/c1-baseline-vs-v2
```
晋级条件（全部满足才建议 merge v2）：目标边界（uncertain 弃权、L2 过度归因）**改善**；Layer 1 核心指标**不下降 > 1pp**；非目标边界无显著 FP 增长；**Holdout 达标**（Holdout 只在 v2 定稿时跑一次）；所有新增错误进 diff 报告。

## 4. 前置条件与数据诚实性（必须处理，别跳过）

- 上一阶段的冻结数据是 **accept-all 探索版（未人审），刻意没入 Git**。要做**可信**对比，需先有一份**人工复核过**的 `c1-v1` 数据集：
  - 路径 A（推荐）：人已复核 `review_queue.csv` → `build_dataset` 冻结 `c1-v1` 入 Git → 再跑 baseline+v2。
  - 路径 B（先看方向）：在探索版数据上先跑一版 v2 对比，**结果标注为 preliminary**，不得作为 merge 依据。
- **去偏提醒**：baseline 是同模型闭环，分数虚高。v2 若只在同模型 Mock 上「变好」，不代表线上更好。**强烈建议**：v2 定稿前，用「第三个模型出题」或**真实脱敏 Gold**（PRD §23）重跑一次对比，才算数。

## 5. 交付物

- `evals/prompt_lab/prompts/judges/01_C-1_content_v2.md`（+ 更新 `prompts_manifest.json`：`python scripts/prompt_lab/build_manifest.py`，需把 v2 加入 `build_manifest.py` 的清单）
- `tmp/prompt_lab/comparisons/c1-baseline-vs-v2/`（diff.json + summary.md + fixed/regressed/unchanged_wrong.csv）
- 一页 v2 变更说明：改了哪三处、为何、对应哪个 §17 风险、compare 结果（fixed/regressed 计数 + uncertain 被迫归因率 baseline→v2）
- 若跑了去偏/Gold 复验：附该结果与「Mock vs 真实落差」结论

## 6. 验收

- v2 可被 parser 解析、schema 合法、`pytest backend/tests/prompt_lab` 仍全绿。
- compare 报告显示：**uncertain 被迫归因率明显下降**（目标 < baseline 的 27.5%，理想 ≤15%）、L2 过度归因下降；且 Layer 1 核心指标未回退 > 1pp、无新增边界 FP。
- 任一 v2 新增错误可从 diff 追溯到 case + prompt hash + 模型 + 原始输出。
- 全程未改基线 prompt / 生产代码 / 无泄漏金鑰。

## 7. 一句话总纲

**先基线、后修改**已完成；本任务是「针对弃权弱点做 prompt v2 + 用现成工具回归」，改的是判定契约的清晰度与正确性，不是刷 Mock 分数——且**任何 merge 决定都要等去偏/真实 Gold 复验**。

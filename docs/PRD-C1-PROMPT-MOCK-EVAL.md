# PRD：C-1「商品内容」单域 Prompt Mock 数据生成与评测实验室

> 状态：Ready for implementation  
> 版本：v1.0  
> 日期：2026-07-13  
> 第一阶段目标域：C-1 商品内容  
> 用途：可直接交给新的编码 AI 实施  
> 硬约束：本期只建设离线 Prompt Lab，不修改生产判决链路、数据库和前端

## 1. 背景

项目计划把六大归因域从「一次竞争式多分类」演进为「六个可独立调试的单域判官」。目前有 7 份候选 Prompt：

1. `00_polarity.md`：极性判官。
2. `01_C-1_content.md`：商品内容单域判官。
3. `02_C-2_quality.md`：商品品质单域判官。
4. `03_C-3_supplier.md`：供应商履约单域判官。
5. `04_C-4_platform.md`：平台与系统单域判官。
6. `05_C-5_service.md`：客服营运单域判官。
7. `06_C-6_customer.md`：理解期待单域判官。

本期只验证 `01_C-1_content.md`；其他五个单域 Prompt 只用于建立 C-1 的近邻责任边界。`00_polarity.md` 暂不串入 C-1 单元评测，避免极性错误污染 C-1 Prompt 的诊断结果。

仓库旧有的 mock 测试集评测脚本服务于旧的六域竞争式分类，不能独立评估 C-1 的域命中、L2 选码、证据落地和棄权，因此本期新建隔离的 Prompt Lab，不在旧脚本上堆叠分支（该批旧脚本已清退）。

## 2. 核心目标

对一条已知包含负向问题的评论，验证 C-1 判官能否稳定回答：

1. 是否存在至少一个属于 C-1 商品内容的问题；
2. 命中时是否选择正确的 C-1 L2 code；
3. 是否提供评论原文中的逐字证据；
4. 不属于时是否返回空 `attributions`；
5. 面对 C-2～C-6 近邻问题、混合表达和对抗表达时是否守住责任边界。

交付后的工具必须支持：

```text
测试计划
  → Mock Generator 生成候选样本
  → Mock Auditor 独立审核
  → 人工复核队列
  → 冻结 Dev / Holdout 数据集
  → 运行 C-1 baseline Prompt
  → 输出指标与逐条错误
  → 替换候选 Prompt
  → 输出 baseline vs candidate 回归差异
```

## 3. 非目标

- 不修改 `backend/app/judge/prejudge.py` 或生产调用链。
- 不评测 C-2～C-6 自身准确率。
- 不把 `00_polarity.md` 的错误计入 C-1 指标。
- 不建设前端、不改数据库、不做模型微调。
- 不让工具自动修改 C-1 Prompt。
- 不用被测 C-1 判官反向筛选 Mock 样本。
- 不把 AI Mock 分数宣称为真实线上准确率。
- 不以模型自报 `confidence` 替代正确性标签。

## 4. 判定契约

### 4.1 域命中

当前 C-1 Prompt 没有 `belongs` 字段，评测器按以下规则转换：

```text
attributions.length > 0  → predicted_domain_hit = true
attributions.length == 0 → predicted_domain_hit = false
```

### 4.2 合法 L2

```text
C-1-1 商品定位
C-1-2 行程流程
C-1-3 费用信息
C-1-4 集合信息
C-1-5 使用／兑换
C-1-6 限制与风险
C-1-7 退改与服务承诺
```

### 4.3 明确正例

评论文本本身必须明确指称商品页／购买页／介绍／说明／凭证的信息写错、缺漏、模糊、前后矛盾、夸大或误导，并满足「只修改页面信息即可避免问题」。不能依赖 Mock 文本外的商品页、订单或现场资料。

### 4.4 明确负例

评论存在负向问题，但责任明确属于：

- C-2：交付物本身品质差；
- C-3：页面已清楚，现场执行未履约；
- C-4：说明存在，但系统、开通、核销或平台功能失败；
- C-5：页面规则存在，但售后客服处理不当；
- C-6：页面清楚，问题来自旅客误读、主观期待、自身或外力。

负例不得同时包含一个独立成立的 C-1 问题。

### 4.5 不确定样本

以下只能标为 `uncertain`，不得强塞 true／false：

- 「时间和描述不一样」，无法判断页面写错还是现场偏离；
- 「到现场才知道要付费」，没有说明页面是否揭露；
- 「兑换很麻烦」，无法判断页面没说明、系统故障还是旅客没看；
- 责任判断需要查看真实商品页或订单。

不确定样本用于测试棄权行为，不进入主要二分类 Precision／Recall 分母。

### 4.6 极性输入

C-1 独立评测直接使用数据集的 `input_polarity=negative|neutral`，不调用 `00_polarity.md`。纯正向评论只作为少量防御性样本，不作为主要负例，因为真实链路中它应被极性层提前拦截。

## 5. 数据规模与覆盖

### 5.1 Layer 1：规则单元测试，共 130 条

正例 70 条：每个 L2 10 条。

| L2 | 数量 | 覆盖 |
|---|---:|---|
| C-1-1 | 10 | 名称、摘要、特色、图片或所在地误导 |
| C-1-2 | 10 | 时长、步骤、景点、交通流程写错／缺漏 |
| C-1-3 | 10 | 门票、必付费用、儿童价格等未揭露 |
| C-1-4 | 10 | 集合时间、地点、地图、方式模糊／矛盾 |
| C-1-5 | 10 | 使用、兑换、凭证、证件要求未说明 |
| C-1-6 | 10 | 年龄、健康、体能、天候、成团条件未揭露 |
| C-1-7 | 10 | 退改、出单 SLA、未履约补救未说明 |

每个 L2 的 10 条至少包括：3 条直接表达、2 条口语、2 条委婉、1 条反问、1 条轻微噪声、1 条 `neutral` 混合评论。

负例 60 条：

| 真实责任 | 数量 | C-1 预期 |
|---|---:|---|
| C-2 商品品质 | 10 | 空 `attributions` |
| C-3 供应商履约 | 10 | 空 `attributions` |
| C-4 平台与系统 | 10 | 空 `attributions` |
| C-5 客服营运 | 10 | 空 `attributions` |
| C-6 理解期待 | 10 | 空 `attributions` |
| 纯正向／无问题 | 10 | 空；仅防御性统计 |

### 5.2 Layer 2：边界与对抗测试，共 210 条

1. 最小对照组 126 条：`7 L2 × 3 主要边界 × 3 组 pair × 2 条`。
2. 混合评论 28 条：每个 L2 4 条。
3. 不确定／证据不足 28 条：每个 L2 4 条。
4. 对抗与鲁棒性 28 条：每个 L2 4 条。

每个 contrast pair 只能改变一个责任事实，例如：

```text
A / C-1=true：页面没说明行程时长。
B / C-1=false：页面写明时长，但现场提前结束。
contrast_key：页面是否已明确说明时长。
```

两条必须共享 `contrast_pair_id`，Dev／Holdout 切分时不得拆开。

对抗样本覆盖：否定反转、先抱怨后澄清、反问、讽刺、简繁混写、多语言、emoji、错别字、长短文本和 Prompt Injection。

### 5.3 C-1 边界矩阵

| C-1 L2 | 主要近邻边界 |
|---|---|
| C-1-1 | C-2 客观品质；C-6-3 主观期待；无明确问题 |
| C-1-2 | C-3-3 现场偏离表定；C-6-3 依表执行但主观嫌赶；证据不足 |
| C-1-3 | C-3-4 现场追加；C-3-7 强迫消费；C-6-2 单纯不值 |
| C-1-4 | C-3-2 司机／导游未到；C-6-6 自己没看；证据不足 |
| C-1-5 | C-4-1 开通失败；C-4-2 核销失败；C-6-6 没看；C-3-1 人员刁难 |
| C-1-6 | C-4-2 资格卡关；C-3-4 临时取消；C-3-6 应变失职；C-6-4/5 外力；C-6-6 没读 |
| C-1-7 | C-5-1 修改未落实；C-5-2 退款争议；C-5-3 客服回应；C-3-4 已承诺未履行 |

边界矩阵必须编码为可读 plan，Generator 按格生成，禁止一次自由生成 100 条。

## 6. 数据模型

### 6.1 CandidateCase JSONL

```json
{
  "case_id": "c1-l2-contrast-c12-c33-001-a",
  "domain_under_test": "C-1",
  "layer": 2,
  "text": "页面只写半日游，完全没有说明实际几个小时。",
  "input_polarity": "negative",
  "expected_domain": "true",
  "expected_l2_codes": ["C-1-2"],
  "forbidden_l2_codes": [],
  "expected_evidence_quotes": ["页面只写半日游，完全没有说明实际几个小时"],
  "case_family": "contrast_pair",
  "expression_variant": "direct",
  "difficulty": "hard",
  "language": "zh-tw",
  "boundary_with": "C-3-3",
  "contrast_pair_id": "c1-c12-vs-c33-001",
  "contrast_key": "页面是否已明确说明时长",
  "label_reason": "页面时长信息缺漏，修改页面即可避免",
  "generator_model": "<model-id>",
  "generator_request_id": "<request-id>",
  "generation_plan_id": "c1-layer2-c12-vs-c33",
  "origin": "ai_generated",
  "status": "candidate"
}
```

约束：

- `case_id` 全局唯一且稳定；
- `expected_domain` 为字符串枚举 `true|false|uncertain`；
- true 时 `expected_l2_codes` 为 1～2 个，其他情况必须为空；
- `expected_evidence_quotes` 必须逐字存在于 `text`；
- `contrast_pair_id` 非对照样本为 `null`；
- `origin=ai_generated|human_edited|human_authored`；
- `status=candidate|audited|review_required|accepted|rejected`。

### 6.2 AuditResult JSONL

```json
{
  "case_id": "...",
  "label_supported": true,
  "ambiguous": false,
  "self_contained": true,
  "contains_independent_c1_issue": true,
  "suggested_domain": "true",
  "suggested_l2_codes": ["C-1-2"],
  "evidence_quotes_valid": true,
  "near_duplicate": false,
  "audit_reason": "文本明确指出页面未说明时长",
  "auditor_model": "<model-id>",
  "auditor_request_id": "<request-id>",
  "status": "accepted"
}
```

以下自动进入 `review_required`：Generator/Auditor 标签或 L2 不一致、`ambiguous=true`、不自包含、证据不落地、负例含独立 C-1、近重复或 Schema 非法。

### 6.3 FrozenCase JSONL

冻结集只保留评测字段、审核元数据和 `dataset_version/split/human_reviewed`，不保留 Generator/Auditor 长推理。任何修改产生新 dataset version 和 SHA-256。

### 6.4 JudgeRunResult JSONL

每次调用至少保存：

```text
run_id, case_id, repeat_index, prompt_version, prompt_sha256,
model, request_id, raw_output, predicted_domain_hit,
predicted_l2_codes, schema_valid, evidence_grounded,
latency_ms, input_tokens, output_tokens, attempts, error
```

## 7. Generator 规格

Generator 接收独立测试规格，而不是完整被测 Prompt：C-1 标签契约、目标 L2、近邻定义、预期标签、难度、表达变体、contrast key、数量和禁止条件。

必须满足：

- 文本自然且像真实旅客评论；
- 标签能从文本自身判断；
- 正例明确出现页面／介绍／说明的信息责任证据；
- 负例明确给出他域责任事实且不含独立 C-1；
- 评论正文不出现 `C-1`、`L2`、`正确答案` 等泄漏；
- 不把现有 Prompt 示例仅做同义改写；
- 对照组只改变 `contrast_key`；
- 无法生成确定标签时返回失败，不得硬造。

每次调用只生成 3～5 条，粒度为：一个 L2＋一个 expected domain＋一个 boundary＋一个 variant＋一个 difficulty。

去重：先做 Unicode NFKC、合并空白后的 exact hash；语义近重复由 Auditor 标记，本期不强制增加 embedding 依赖。

## 8. Auditor 与人工审核

Auditor 是审题器，不是被测判官。它检查：标签是否自包含、是否有第二种合理解释、L2 是否正确、负例是否暗含 C-1、证据是否逐字存在、pair 是否只改一个事实、文本是否自然和重复。

隔离要求：

- Generator、Auditor、Judge 使用独立 Prompt；
- Generator 不看 Judge 输出；
- Auditor 不使用被测 C-1 Prompt；
- Generator 与 Judge 默认不得使用同一模型 snapshot；
- Auditor 推荐使用不同于 Generator 的模型或 snapshot；
- 若只能用同一模型，manifest 标注局限并提高人工抽检比例。

必须人工审核：所有 `review_required`、所有 `uncertain`、所有 contrast pair，以及其余自动通过样本的分层随机 20%。

工具输出 `review_queue.csv`，支持 `accept|edit|reject`。人工编辑后标记 `origin=human_edited`。

## 9. 数据冻结与防泄漏

- 70% Dev：可查看逐条错误并调 Prompt；
- 30% Holdout：只在候选 Prompt 定稿时运行；
- 按 layer、domain、L2、boundary、family 分层；
- 同一 `contrast_pair_id` 必须进入同一 split；
- 固定 split seed；
- 检查 case id、exact text、contrast pair 均无跨集泄漏；
- 冻结 JSONL 和 manifest 进入 Git，并记录 SHA-256；
- 根据 Holdout 具体错误继续调 Prompt 时，必须建立新一轮 Holdout。

## 10. Judge Runner

### 10.1 Markdown 解析

读取 `## System`、`## User`、`## Schema` 各自第一个 fenced block，并替换：

```text
{POLARITY} → FrozenCase.input_polarity
{TEXT}     → FrozenCase.text
```

缺段、JSON Schema 非法或占位符缺失时立即失败，不得静默使用空 Prompt。

### 10.2 OpenAI 调用

默认使用 Responses API 和 strict Structured Outputs。官方 Python SDK 支持 `client.responses.parse(..., text_format=PydanticModel)` 获取 `output_parsed`，也支持 strict JSON Schema。SDK 细节集中封装在单一 gateway，上层脚本不得散落供应商参数。参考：[Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)、[Responses API 文本生成](https://developers.openai.com/api/docs/guides/text?api-mode=responses)。

同步模式用于小批调试；Batch 模式用于完整数据集、多 Prompt 或多模型评测。Batch 为可选后端，不阻塞 MVP。

Batch 实现要求：endpoint `/v1/responses`；唯一 `custom_id=case_id+repeat+prompt_hash_short`；不依赖输出顺序；保存 batch/file/error ids；支持失败续跑。官方文档确认 Batch 支持 Responses、结果顺序不保证一致，当前单批上限 50,000 请求／200 MB。参考：[Batch API](https://developers.openai.com/api/docs/guides/batch)。

### 10.3 模型配置

禁止硬编码“最新模型”，使用 CLI／环境变量：

```text
PROMPT_LAB_GENERATOR_MODEL
PROMPT_LAB_AUDITOR_MODEL
PROMPT_LAB_JUDGE_MODEL
```

CLI 优先。正式 baseline 推荐固定 snapshot，所有运行记录实际 model id。当前 OpenAI 模型指导可作为选型参考，但必须以账号可用性和本项目实测为准：[Model guidance](https://developers.openai.com/api/docs/guides/latest-model)、[Models](https://developers.openai.com/api/docs/models)。

### 10.4 重复、失败与续跑

- 默认 repeats=3，必须真实调用且禁用项目 exact-match cache；
- 保存每次结果，不用多数投票掩盖不稳定；
- 429／5xx 指数退避，最多 5 次；
- Schema 失败、拒答、空输出分别记录，不能猜测修复；
- 单条失败不终止整批；
- `--resume` 以 case＋repeat＋prompt hash＋model 跳过成功项。

## 11. 指标

### 11.1 域二分类

只使用 expected true／false，输出 Precision、Recall、Specificity、F1、FPR、FNR。纯正向防御样本独立统计。

### 11.2 L2

只对 expected true 计算：Exact Set Accuracy、Any-hit Accuracy、per-L2 P/R/F1、Over-attribution、Under-attribution、Duplicate rate。

### 11.3 证据

- Evidence Grounding：每条 quote 是否为原文逐字子串；
- Expected Evidence Overlap；
- Empty Evidence Rate；
- 逐条查看 evidence-to-L2 consistency，不再调用第二个在线 Judge 打分。

### 11.4 稳定性

Domain Full Agreement、L2 Set Full Agreement、Pairwise Agreement、confidence range/std、true/false flip cases。

### 11.5 Uncertain 与 contrast pair

- uncertain：Abstain Rate、Forced Attribution Rate、被强制归入的 L2 分布；
- contrast：Pair Both Correct、正侧／负侧 accuracy、按 contrast key 的失败分布。

### 11.6 切片

所有核心指标支持按 layer、domain、L2、boundary、family、variant、difficulty、polarity、language、origin、split 切片。

## 12. 验收阈值

工程硬门槛：

- FrozenCase Schema 合法率 100%；
- 非空归因证据逐字落地率 100%；
- Prompt、数据、模型和参数可追溯率 100%；
- Dev／Holdout 无 id、exact text、pair 泄漏；
- dry-run 零外部 API。

Layer 1 初始目标：Domain Recall ≥95%、Specificity ≥95%、L2 Exact ≥90%、neutral C-1 Recall ≥90%、Domain Full Agreement ≥95%。

Layer 2 初始目标：Domain Precision/Recall ≥90%、Pair Both Correct ≥85%、每个主要 boundary FPR ≤15%、uncertain Forced Attribution ≤30%、Domain Full Agreement ≥90%。

这些是 Mock 工程目标，不是最终上线门槛。上线前必须用真实 Gold 重新定阈值。

候选 Prompt 相对 baseline 的晋级条件：目标边界改善；Layer 1 核心指标不得下降超过 1 个百分点；非目标边界无显著 FP 增长；Holdout 达标；所有新增错误进入 diff 报告。

## 13. 报告

每次运行输出：

```text
run_manifest.json
raw_results.jsonl
metrics.json
summary.md
errors.csv
unstable_cases.csv
boundary_matrix.csv
contrast_pairs.csv
```

`summary.md` 包含数据和 Prompt hash、模型、请求/token/失败/耗时、Layer 1/2、各 L2、各边界、不确定样本、稳定性、Top 错误和是否通过门槛。

Prompt diff 额外输出：fixed、regressed、unchanged_wrong、confidence shift、slice delta、成本和延迟变化。

## 14. 目录结构

```text
evals/prompt_lab/
  README.md
  prompts/
    judges/00_polarity.md ... 06_C-6_customer.md
    generators/c1_generator.md
    generators/c1_auditor.md
  plans/c1_layer1_plan.json
  plans/c1_layer2_plan.json
  datasets/c1/
    README.md
    c1-v1-dev.jsonl
    c1-v1-holdout.jsonl
    c1-v1-manifest.json

scripts/prompt_lab/
  schemas.py
  prompt_parser.py
  openai_gateway.py
  generate_cases.py
  audit_cases.py
  build_dataset.py
  evaluate_prompt.py
  compare_runs.py
  report.py

backend/tests/prompt_lab/
  test_schemas.py
  test_prompt_parser.py
  test_split.py
  test_metrics.py
  test_evidence.py
  test_resume.py
```

运行中间产物放 `tmp/prompt_lab/`，不入 Git。冻结数据、计划和 baseline Prompt 入 Git。

首次实现将用户提供的 7 份 Markdown 原样复制到 `evals/prompt_lab/prompts/judges/` 并记录 SHA-256；不得在导入时顺手修 Prompt，baseline 必须先被评测。

## 15. CLI 契约

```bash
# 生成
python scripts/prompt_lab/generate_cases.py \
  --plan evals/prompt_lab/plans/c1_layer1_plan.json \
  --model "$PROMPT_LAB_GENERATOR_MODEL" \
  --out tmp/prompt_lab/c1-layer1-candidates.jsonl \
  --workers 4 --resume

# 审核
python scripts/prompt_lab/audit_cases.py \
  --input tmp/prompt_lab/c1-layer1-candidates.jsonl \
  --model "$PROMPT_LAB_AUDITOR_MODEL" \
  --out tmp/prompt_lab/c1-layer1-audits.jsonl \
  --review-queue tmp/prompt_lab/c1-layer1-review.csv --resume

# 冻结
python scripts/prompt_lab/build_dataset.py \
  --candidates tmp/prompt_lab/c1-candidates.jsonl \
  --audits tmp/prompt_lab/c1-audits.jsonl \
  --human-review tmp/prompt_lab/c1-human-review.csv \
  --dataset-version c1-v1 \
  --out-dir evals/prompt_lab/datasets/c1 --split-seed 42

# 评测
python scripts/prompt_lab/evaluate_prompt.py \
  --prompt evals/prompt_lab/prompts/judges/01_C-1_content.md \
  --dataset evals/prompt_lab/datasets/c1/c1-v1-dev.jsonl \
  --model "$PROMPT_LAB_JUDGE_MODEL" \
  --repeats 3 --out tmp/prompt_lab/runs/c1-baseline-dev \
  --workers 8 --no-cache --resume

# 对比
python scripts/prompt_lab/compare_runs.py \
  --baseline tmp/prompt_lab/runs/c1-baseline-dev \
  --candidate tmp/prompt_lab/runs/c1-v2-dev \
  --out tmp/prompt_lab/comparisons/c1-baseline-vs-v2
```

生成／审核支持 `--dry-run --limit --plan-id --workers --resume --model`。评测支持 `--layer --case-id --slice field=value --limit --repeats --sync|--batch --dry-run --resume --no-cache`。

成本保护：默认 limit=5 或要求显式 `--all`；dry-run 打印请求数；全量需 `--confirm-cost`；支持最大请求预算；Batch 只能显式开启。

## 16. OpenAI SDK 工程要求

复用 `backend/pyproject.toml` 的 `openai` 和 `pydantic`。若现有版本不支持所需 Responses parse API，应单独升级并跑现有 LLM gateway 回归测试，不得静默破坏生产客户端。

`openai_gateway.py` 提供稳定接口，至少包括结构化同步生成、Batch submit/poll/download。Generator、Auditor、Judge 全部使用 strict Structured Outputs 和 Pydantic；拒绝额外字段；refusal、incomplete、parse error 分别记录。

每次调用记录 request id、model、case/plan id、prompt hash、attempt、latency、token、error、batch id。不得记录 API key。

## 17. 已知 Prompt 风险：先基线，后修改

编码 AI 不得先修 Prompt，必须用数据跑 baseline。

### 17.1 `❌` 语义冲突

`<domain_boundary>` 中 `❌` 表示「不属于本域，应棄权」；`<facet_catalog>` 中的 `❌误判例` 却常是应触发 C-1 的页面违规写法，例如“未标示或模糊描述”。Layer 1 必须专测。后续候选版本可改为「✅合规写法／🚩违规写法，应命中本面向」。

### 17.2 多问题指令冲突

Prompt 同时写了「取最核心问题」和「列出所有明确问题，最多 2 条」。Layer 2 混合样本必须暴露真实行为，候选版本再统一契约。

### 17.3 外部证据限制

Judge 看不到商品页和订单，只能判断评论是否明确指称页面问题，不能验证页面事实。依赖外部资料的一律标 uncertain，不能把 Generator 假设的后台事实当标准答案。

## 18. 实施阶段与 DoD

### Phase 0：资产与规格

导入 7 Prompt、建立 hash manifest、Layer 1/2 plan、Pydantic schemas、Generator/Auditor Prompt 和 README。DoD：计划数严格为 130/210，静态校验通过。

### Phase 1：生成与审核

完成同步 gateway、Generator、Auditor、exact 去重、review queue、resume/retry/dry-run。DoD：5 条 smoke 从生成到审核全部通过 Schema。

### Phase 2：冻结

完成人工 CSV 导入、分层切分、pair 防泄漏、manifest/hash、质量报告。DoD：Dev/Holdout 无泄漏且覆盖矩阵匹配。

### Phase 3：Runner 与指标

完成 Markdown parser、C-1 runner、重复运行、域/L2/证据/稳定性/contrast 指标、切片和错误 CSV。DoD：fixture 指标精确正确，5 条 live smoke 跑通。

### Phase 4：报告与比较

完成 summary、fixed/regressed、门槛判断。DoD：同一数据集可对比两个 Prompt，任一错误可追溯。

### Phase 5：Batch（可选，不阻塞 MVP）

完成 Responses Batch、custom_id 回连、失败续跑。DoD：同步与 Batch 对同一小集产出结构一致。

## 19. 自动化测试

至少覆盖：四类数据 Schema；Markdown 解析与占位符；证据子串；true/false/uncertain 分母；L2 exact/extra/missing；pair 不跨 split；切分可复现；重复 id 拒绝；resume；429/5xx retry；Schema error 不等于棄权；Batch 乱序回连；报告 fixture；dry-run 零 API。

## 20. 安全与隐私

- 本期只使用 Mock，不放真实 PII；
- API key 只从环境变量／现有安全配置读取；
- 原始响应只存 `tmp/prompt_lab`；
- Prompt Injection 样本只能当待判文本，不能执行；
- 未来引入真实评论前必须另做脱敏和权限设计。

## 21. 最终交付清单

- [ ] 7 份 baseline Prompt 与 hash manifest；
- [ ] Layer 1/2 plans；
- [ ] Generator/Auditor Prompt；
- [ ] Pydantic 数据模型与 OpenAI gateway；
- [ ] Generator/Auditor/人工审核/冻结 CLI；
- [ ] C-1 Runner 与完整指标；
- [ ] Markdown/JSON/CSV 报告和 Prompt diff；
- [ ] 单元测试与 fake-client 集成测试；
- [ ] 使用 README；
- [ ] 5 条 live smoke 脱敏结果；
- [ ] C-1 baseline Dev 报告；
- [ ] 已知问题和下一步建议。

## 22. 最终验收场景

验收人必须能：检查 130+210 计划；dry-run 且零 API；生成并审核 C-1-2 vs C-3-3 对照样本；导出人工 review；冻结小型 Dev/Holdout；使用 baseline 跑 3 次；查看域/L2/证据/稳定性/pair 指标；不覆盖 baseline 地测试候选 Prompt；得到 fixed/regressed diff；从任一错误追溯 case、Prompt hash、模型和原始输出。

全部完成才视为本 PRD 达成。

## 23. 后续路线

1. 用 baseline 报告设计 C-1 Prompt v2；
2. C-1 Layer 1/2 达标；
3. 引入小批真实匿名 Gold，验证 Mock 与真实数据落差；
4. 抽象 domain plan，扩展到 C-2～C-6；
5. 最后才设计六个单域判官并行接入生产的架构、成本与合并策略。

不得跳过真实 Gold 就用 Mock 分数作为上线依据。

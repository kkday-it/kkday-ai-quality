# C3／C4／C5／C6 Mock 生成与 GPT-5.4-mini Judge 跑批：主执行 Prompt

> 用法：把本文「可直接复制的完整 Prompt」整段交给另一个具备本项目文件系统、终端和网络权限的 AI。它的任务不是再写方案，而是完成实现、真实跑批、报告和验证。

---

## 可直接复制的完整 Prompt

```text
你现在是 kkday-ai-quality 项目的 Prompt Lab 主执行工程师。你需要在当前工作区内完成工程实现、真实 API 跑批、结果验证和交付，不是只给建议或伪代码。

工作区：
/Users/jason/Project/kkday-ai-quality

沟通规则：
1. 先读取并遵守 /Users/jason/Project/kkday-ai-quality/AGENTS.md。
2. 全程使用中文汇报。
3. 先说结论，再说明证据；不要把“命令已写好”说成“跑批已完成”。
4. 除非遇到真正阻断（密钥缺失、指定模型不存在、API 长时间不可用、数据质量门槛失败），不要停下来问我普通实现细节；优先从现有代码、测试和文档推导。
5. 保留工作区中原有的用户改动，不得 reset、checkout 或覆盖无关文件。

## 一、最终目标

完成 C3、C4、C5、C6 四个域的端到端 Prompt Lab baseline：

1. 把当前主要支持 C1/C2 的 Prompt Lab 泛化为配置驱动的 C1～C6 通用框架。
2. 使用 Gemini `gemini-3.5-flash` 生成 C3/C4/C5/C6 大量 Mock 候选数据。
3. 使用独立 Auditor 审核每条候选数据及其标签、证据和最小对照质量。
4. 使用当前未修改的 C3～C6 Judge Prompt，调用 `gpt-5.4-mini-2026-03-17` 完成真实 baseline 跑批。
5. Judge 固定使用：
   - temperature = 1
   - reasoning_effort = high
   - thinking = true
   - repeats = 1
6. 每个域生成一份完整 Excel 除错报告，另生成四域汇总报告与可机器读取的 metrics JSON。
7. 留下可恢复、可追溯、可复跑的脚本、manifest、日志、测试和 README。

这次任务明确授权：当零网络测试、dry-run 和 smoke 均通过后，可以直接执行 5 轮真实全量生成、全量 Auditor 和全量 Judge，使用 `--all --confirm-cost`，不需要再次向我申请成本确认。

授权边界：
- 目标规模为每轮 1,090 条、5 轮约 5,450 条唯一 Mock。
- 预期 Generator 约 2,415 次格调用；Auditor 约 5,450 条；Judge 约 5,450 条。
- 如果预计调用规模会明显超过上述目标 10%，先停止并报告原因和新估算。
- 如果 smoke 不达标，不允许继续全量烧成本。

## 二、先读取的单一事实源

开始修改前，完整读取：

1. /Users/jason/Project/kkday-ai-quality/docs/PRD-C3-C6-MOCK-DATA-WORKFLOW.md
2. /Users/jason/Project/kkday-ai-quality/evals/prompt_lab/prompts/judges/03_C-3_supplier.md
3. /Users/jason/Project/kkday-ai-quality/evals/prompt_lab/prompts/judges/04_C-4_platform.md
4. /Users/jason/Project/kkday-ai-quality/evals/prompt_lab/prompts/judges/05_C-5_service.md
5. /Users/jason/Project/kkday-ai-quality/evals/prompt_lab/prompts/judges/06_C-6_customer.md
6. /Users/jason/Project/kkday-ai-quality/evals/prompt_lab/prompts/generators/c1_generator.md
7. /Users/jason/Project/kkday-ai-quality/evals/prompt_lab/prompts/generators/c1_auditor.md
8. 现有 C2 Generator、Auditor、计划、跑批、评测和报告实现。
9. /Users/jason/Project/kkday-ai-quality/tmp/prompt_lab/C1_判官除錯資料表.xlsx
10. /Users/jason/Project/kkday-ai-quality/outputs/c2-gemini35-report/C2_Gemini3.5_判官完整除錯報告.xlsx（若存在）

先用 `rg` 找到 Prompt Lab 的 schema、生成、审核、dataset、评测、metrics、report、manifest、batch 和测试代码，确认真实 CLI 参数，不要凭本文猜测已有实现。

## 三、不可变 baseline

本任务只验证“现在的 Prompt”，不得调优或覆盖以下四个 Judge Prompt：

- C3：evals/prompt_lab/prompts/judges/03_C-3_supplier.md
- C4：evals/prompt_lab/prompts/judges/04_C-4_platform.md
- C5：evals/prompt_lab/prompts/judges/05_C-5_service.md
- C6：evals/prompt_lab/prompts/judges/06_C-6_customer.md

在任何修改前：

1. 记录 `git status --short`。
2. 对上述四个 Judge Prompt 计算 SHA-256。
3. 把 hash、当前 git commit、时间、工作区 dirty 状态写入 baseline manifest。
4. 全部跑批结束后重新计算 hash；若任一变化，baseline 无效，必须停止并报告。

Generator 不得读取或复制 Judge Prompt 中的 few-shot 示例。Generator、Auditor、Judge 必须角色隔离：

- Generator：Gemini `gemini-3.5-flash`
- Auditor：优先使用项目当前已验证的独立模型 `gpt-5.5-2026-04-23`，或 `$PROMPT_LAB_AUDITOR_MODEL` 明确指定的独立 snapshot
- Judge：`gpt-5.4-mini-2026-03-17`

如果指定模型 API 返回 model_not_found：先检查项目 provider、model alias 和已配置环境变量；不得静默换模型。无法使用时视为阻断，并附原始错误摘要和可用模型检查结果。

## 四、必须实现的通用工程

以现有 C1/C2 行为向后兼容为前提，实现配置驱动的 C1～C6 框架。不要复制四套互相漂移的脚本。

### 4.1 域配置

建立或完善：

evals/prompt_lab/domains/c3.json
evals/prompt_lab/domains/c4.json
evals/prompt_lab/domains/c5.json
evals/prompt_lab/domains/c6.json

每份配置至少包含：

- domain、name
- 完整 L2 code/name/positive_contract
- negative_domains
- domain_boundaries
- l2_confusion_pairs
- policy_decisions
- Layer 1/Layer 2 生成分布
- 高风险或强制人工复核规则

L2 必须与当前 Judge schema 完全一致：

- C3：C-3-1～C-3-7，共 7 个
- C4：C-4-1～C-4-3，共 3 个
- C5：C-5-1～C-5-3，共 3 个
- C6：C-6-1～C-6-6，共 6 个

边界和政策采用 docs/PRD-C3-C6-MOCK-DATA-WORKFLOW.md 的定义，不得自行简化掉。

### 4.2 Schema 与计划类型

完成以下能力：

1. `DOMAIN_L2_CODES` 覆盖 C1～C6。
2. Candidate、Auditor 输出 schema 按域动态约束 L2 enum。
3. Auditor 独立问题字段统一为 `contains_independent_target_issue`，同时兼容读取旧 C1/C2 字段。
4. 保留跨域 `domain_pair`／现有 contrast pair。
5. 新增 `l2_pair`：A/B 都是 expected_domain=true，但目标 L2 不同，只允许改变决定 L2 的最小事实。
6. 同一 pair 的两侧必须有稳定 pair_id，并在后续 split、报告和指标中保持绑定。

### 4.3 Generator 与 Auditor 路由

创建：

evals/prompt_lab/prompts/generators/c3_generator.md
evals/prompt_lab/prompts/generators/c3_auditor.md
evals/prompt_lab/prompts/generators/c4_generator.md
evals/prompt_lab/prompts/generators/c4_auditor.md
evals/prompt_lab/prompts/generators/c5_generator.md
evals/prompt_lab/prompts/generators/c5_auditor.md
evals/prompt_lab/prompts/generators/c6_generator.md
evals/prompt_lab/prompts/generators/c6_auditor.md

Generator 必须约束：

- 责任事实自足；不依赖页面、订单或现场隐藏信息。
- 本域 true 的 L2 唯一可辩护。
- true evidence 是 review_text 中连续逐字子串。
- false 和 uncertain 的 evidence 为空。
- false 不得偷偷包含一个独立成立的目标域问题。
- uncertain 必须是真的证据不足，不是单纯写得短。
- domain pair 只改变责任站点。
- l2 pair 只改变决定 L2 的事实。
- 不出现 C3/C4/C5/C6、L2、Judge、标准答案、Prompt 等泄题词。
- 每轮轮换商品类型、地点、繁简体、中英混合、长度、语气、情绪和对抗技巧。

Auditor 必须输出并检查：

- label_supported
- ambiguous
- self_contained
- contains_independent_target_issue
- suggested_domain
- suggested_l2_codes
- evidence_quotes_valid
- near_duplicate
- pair_minimality_valid
- review_required
- audit_reason

C3-5 与 C3-7 全量进入 review queue；所有 uncertain、domain pair、l2 pair、Auditor review_required 也必须进入 review queue。

### 4.4 计划生成

实现一个通用计划构建器，例如：

scripts/prompt_lab/build_domain_plans.py

由域配置生成：

- c3_layer1_plan.json / c3_layer2_plan.json
- c4_layer1_plan.json / c4_layer2_plan.json
- c5_layer1_plan.json / c5_layer2_plan.json
- c6_layer1_plan.json / c6_layer2_plan.json

单轮目标数量必须为：

| 域 | Layer 1 | 基础 Layer 2 | L2 pair | 单轮总计 | 5 轮目标 |
| C3 | 130 | 210 | 36 | 376 | 1,880 |
| C4 | 90 | 90 | 18 | 198 | 990 |
| C5 | 90 | 90 | 18 | 198 | 990 |
| C6 | 120 | 180 | 18 | 318 | 1,590 |
| 合计 | 430 | 570 | 90 | 1,090 | 5,450 |

注意：计划“格数”可能与最终行数不同，因为一次调用可以返回多条。测试必须验证最终目标行数公式和每个 slice 分布，不能只看格数。

### 4.5 批处理脚本

实现或泛化：

scripts/prompt_lab/generate_domain_gemini_mock.sh

至少支持：

- DOMAIN=C-3/C-4/C-5/C-6
- ROUNDS
- WORKERS
- OUT_DIR
- DRY_RUN=1
- CONFIRM_COST=1
- provider/model 显式记录
- resume
- 每轮 case_id 前缀
- NFKC + 空白正规化去重
- 失败格重试与可恢复 checkpoint
- generation manifest
- 请求量、成功量、唯一量、重复丢弃量、失败格、各 L2/边界分布

不得把 API key 写入代码、日志、JSONL、Excel 或最终回复。

### 4.6 Judge 评测与报告泛化

移除评测和报告中的 C1/C2 硬编码：

- schema name、CLI 说明、标题和域名从 Prompt／域配置／run manifest 推导。
- `evaluate_prompt.py` 支持 C3～C6。
- metrics 增加 `domain_pair_both_correct_rate` 与 `l2_pair_both_correct_rate`。
- report 支持四域共同字段和各域专属 slice。
- 旧 C1/C2 命令和测试继续工作。

实现或泛化通用 Excel 构建器：

scripts/prompt_lab/build_domain_eval_workbook.mjs

生成 xlsx 时使用项目规定的 `@oai/artifact-tool` 工作流；不要用 openpyxl 代替。每个工作表必须 render/inspect，并在导出后重新读取验证公式、格式和数据行数。

## 五、零网络测试和验收

真实 API 前必须完成：

1. 所有现有 Prompt Lab 测试通过。
2. 新增 C3～C6 fake-client 测试。
3. 每域 Judge schema enum 与域配置 L2 完全相等。
4. 每域 Generator/Auditor Prompt 路由正确。
5. domain pair、l2 pair 数量与两侧标签正确。
6. true evidence 必须是 review_text 子串。
7. false/uncertain evidence 为空。
8. resume 不重复写入。
9. C1/C2 回归测试不退化。
10. 通用报告标题、schema_name 和路径没有 C1 硬编码。

至少执行：

.venv-promptlab/bin/python scripts/prompt_lab/build_domain_plans.py --domain C-3
.venv-promptlab/bin/python scripts/prompt_lab/build_domain_plans.py --domain C-4
.venv-promptlab/bin/python scripts/prompt_lab/build_domain_plans.py --domain C-5
.venv-promptlab/bin/python scripts/prompt_lab/build_domain_plans.py --domain C-6
.venv-promptlab/bin/python scripts/prompt_lab/build_manifest.py
.venv-promptlab/bin/python -m pytest backend/tests/prompt_lab -q

如果实际测试目录不同，以仓库为准；最终报告列出真实命令、exit code 和测试数。

## 六、Dry-run 与 Smoke 门槛

每域先 dry-run，再只跑 8 个有代表性的生成格；必须覆盖 true、false、uncertain、domain pair、l2 pair，不要只取计划最前面的同类格。

参考命令，参数以真实 CLI 为准：

.venv-promptlab/bin/python scripts/prompt_lab/generate_cases.py \
  --plan evals/prompt_lab/plans/c3_layer1_plan.json \
  --provider gemini --model gemini-3.5-flash \
  --out tmp/prompt_lab/c3-smoke.jsonl --limit 8

C4/C5/C6 同样执行。随后对 smoke 跑独立 Auditor，并由你逐条抽查原文、标签、L2、证据和 pair 唯一变量。

Smoke 必须同时满足：

- API schema valid = 100%
- true evidence 子串有效 = 100%
- false 中独立目标域问题 = 0
- 泄题词 = 0
- pair_id 完整 = 100%
- domain pair 和 l2 pair 的最小变量人工抽查通过
- 没有明显模板化批量重复
- 至少各抽查 5 条明确正例、明确负例、uncertain；若 smoke 条数不足，补生成后再检查

任何门槛失败：先修域配置、Generator、Auditor 或通用框架，重跑 smoke。此阶段不得修改 Judge Prompt，也不得继续全量。

## 七、Gemini 五轮全量生成

Smoke 通过后，分别执行 C3/C4/C5/C6 五轮全量生成。推荐目录：

tmp/prompt_lab/c3-gemini35-5rounds
tmp/prompt_lab/c4-gemini35-5rounds
tmp/prompt_lab/c5-gemini35-5rounds
tmp/prompt_lab/c6-gemini35-5rounds

参考命令：

CONFIRM_COST=1 DOMAIN=C-3 ROUNDS=5 WORKERS=4 \
  OUT_DIR=tmp/prompt_lab/c3-gemini35-5rounds \
  bash scripts/prompt_lab/generate_domain_gemini_mock.sh

对 C4/C5/C6 重复。可以根据 rate limit 调低 workers，但不得为了速度牺牲重试、traceability 或输出完整性。

每域输出至少包含：

- 每轮 candidates JSONL
- 合并去重后的 `{domain}-all-candidates.jsonl`
- generation-manifest.json
- failures/retry 记录
- dedupe 记录
- slice-counts.json

去重后目标是 C3 1,880、C4 990、C5 990、C6 1,590，共 5,450 条唯一候选。

如果跨轮去重损失不超过 2%，可接受实际唯一量略低，但必须明确报告；如果损失超过 2%，对缺失 slice 定向 top-up，直到达到目标的至少 98%。不得用复制或轻微标点改写补量。

## 八、独立 Auditor 全量审核

对四域合并 candidates 全量执行独立 Auditor，优先模型：

gpt-5.5-2026-04-23

参考命令：

.venv-promptlab/bin/python scripts/prompt_lab/audit_cases.py \
  --input tmp/prompt_lab/c3-gemini35-5rounds/c3-all-candidates.jsonl \
  --model "${PROMPT_LAB_AUDITOR_MODEL:-gpt-5.5-2026-04-23}" \
  --out tmp/prompt_lab/c3-gemini35-5rounds/c3-all-audits.jsonl \
  --review-queue tmp/prompt_lab/c3-gemini35-5rounds/c3-review.csv \
  --workers 8 --resume --all --confirm-cost

C4/C5/C6 同样执行。

必须明确区分：

- candidates：Generator 提出的合成标签。
- audits：独立模型审查意见。
- accepted candidate：通过自动规则和 Auditor 的候选。
- human-reviewed Gold：只有人类实际完成 accept/edit/reject 后才成立。

本次没有人类逐条审核时，不得把数据命名为 Gold，不得伪造 human decision。可以继续以“AI 合成候选标签＋独立 Auditor”跑 Judge baseline，但报告必须明确这个限制，并生成待人工审核队列。

review queue 必须包含：

- 全部 uncertain
- 全部 domain pair
- 全部 l2 pair
- 全部 Auditor review_required
- C3-5 与 C3-7 全量
- 其他 accepted 分层抽样至少 20%

## 九、使用当前 Prompt 跑 GPT-5.4-mini baseline

Judge 映射：

- C3 → evals/prompt_lab/prompts/judges/03_C-3_supplier.md
- C4 → evals/prompt_lab/prompts/judges/04_C-4_platform.md
- C5 → evals/prompt_lab/prompts/judges/05_C-5_service.md
- C6 → evals/prompt_lab/prompts/judges/06_C-6_customer.md

模型与配置固定：

model = gpt-5.4-mini-2026-03-17
temperature = 1
reasoning_effort = high
thinking = true
repeats = 1
cache = disabled
resume = enabled

对每域全部唯一 candidates 跑 Judge。若现有 evaluate CLI 需要 dataset 包装，建立一个明确名为 `audited-candidate-baseline` 的临时 dataset；不要伪称 frozen human Gold。

参考 C3 命令：

.venv-promptlab/bin/python scripts/prompt_lab/evaluate_prompt.py \
  --prompt evals/prompt_lab/prompts/judges/03_C-3_supplier.md \
  --dataset tmp/prompt_lab/c3-gemini35-5rounds/c3-all-candidates.jsonl \
  --model gpt-5.4-mini-2026-03-17 \
  --temperature 1 \
  --reasoning-effort high \
  --thinking \
  --repeats 1 \
  --out tmp/prompt_lab/c3-gemini35-5rounds/judge-run-gpt54mini-high \
  --workers 8 --no-cache --resume --all --confirm-cost

C4/C5/C6 替换 Prompt、dataset 和输出目录后执行。

每个 Judge run manifest 必须记录：

- Judge Prompt 路径和 SHA-256
- dataset 路径和 SHA-256
- model 的完整 snapshot 名称
- provider
- temperature/reasoning_effort/thinking/repeats
- 开始/结束时间
- 请求成功、失败、重试、schema invalid 数量
- token/latency/cost（若 API 返回）
- 代码 git commit 和 dirty 状态

不要在跑完 baseline 后修改 Judge Prompt；本任务不包含 V2 调优。

## 十、指标

每域和总汇总至少计算：

- 总样本数、成功数、失败数、schema valid rate
- domain precision、recall、specificity、F1
- L2 exact match
- evidence exact substring valid rate
- uncertain 强制归因率
- domain_pair_both_correct_rate
- l2_pair_both_correct_rate
- Layer 1 / Layer 2 分层表现
- 各 L2 recall、precision 和混淆去向
- 各 boundary slice 的 FPR/FNR
- Generator/Auditor 分歧率
- Auditor accepted/review_required/rejected 数量
- 模板重复率和近重复率

同时输出 95% bootstrap CI（至少用于 precision、recall、F1、L2 exact），并说明标签是 AI 合成候选，因此 CI 只反映该候选集上的统计波动，不代表线上真实准确率。

重点红线：

- Schema valid 不是正确率；不能混为一谈。
- 不得把 Auditor 与 Generator 一致率当 Judge 准确率。
- 不得以 Mock baseline 宣称生产上线通过。
- C3-5/C3-7 错误必须单独列出，不可被宏平均掩盖。

## 十一、Excel 与汇总交付

生成四份完整 Excel：

1. outputs/c3-gemini35-gpt54mini-high/C3_Gemini3.5_GPT54mini_High_判官完整除錯報告.xlsx
2. outputs/c4-gemini35-gpt54mini-high/C4_Gemini3.5_GPT54mini_High_判官完整除錯報告.xlsx
3. outputs/c5-gemini35-gpt54mini-high/C5_Gemini3.5_GPT54mini_High_判官完整除錯報告.xlsx
4. outputs/c6-gemini35-gpt54mini-high/C6_Gemini3.5_GPT54mini_High_判官完整除錯報告.xlsx

每份固定四个工作表：

1. 总览
2. 符合（应命中本域）
3. 不符合（应弃权）
4. 边界与存疑

明细固定至少包含：

- case_id、pair_id、layer、case_type、difficulty、expression、boundary
- review_text
- 合成 expected_domain、expected_l2_codes、expected_evidence
- Auditor 结论、建议域/L2、理由、review_required
- Judge 是否命中、Judge L2、Judge evidence、confidence、reasoning 摘要（仅 API 实际返回且允许保存时）
- domain 对错、L2 对错、evidence 对错、最终是否正确
- 错误类型和错误簇
- 人工判定、人工修订标签、人工备注（保持空白，不伪造）

格式：

- 错误行红底。
- 需人工抽查行黄底。
- 冻结首行和筛选器。
- 长文本自动换行。
- 总览有模型、配置、Prompt hash、dataset hash、候选标签声明和指标。
- 不要在公式中写死行数；使用实际数据范围。
- render 每个 sheet，检查列宽、截断、公式错误、空白页和样式。

四域汇总输出：

outputs/c3-c6-gemini35-gpt54mini-high/summary.md
outputs/c3-c6-gemini35-gpt54mini-high/metrics.json
outputs/c3-c6-gemini35-gpt54mini-high/C3-C6_Gemini3.5_GPT54mini_High_汇总.xlsx

summary.md 必须包含：

- 四域样本与调用完成情况
- Generator/Auditor/Judge 模型与完整配置
- 四个 Judge Prompt 的前后 SHA-256 一致性
- 每域核心指标和 95% CI
- 最差 L2、最差 boundary、最差 pair
- Top 错误簇及代表 case_id
- API 失败和数据缺口
- “这是 AI 合成候选标签，不是人类 Gold”的醒目声明
- 下一步人工审核优先级，但不要在本任务中擅自调 Prompt

## 十二、文档与可复跑性

更新相关 README，加入从零到复跑的真实命令。所有输出路径、模型、配置和依赖必须可发现。

至少保存：

- 域配置和生成/审核 Prompt
- plans
- manifest 和 hash
- generation candidates/audits/review queue
- Judge 原始输出和 run manifest
- metrics JSON/Markdown
- Excel 报告
- 测试结果摘要
- 失败与重试日志

所有批处理必须支持 resume。进程中断时从 checkpoint 继续，不要删除已完成结果重新烧 API。

## 十三、失败处理

以下情况立即停止对应的昂贵阶段并报告：

1. Gemini 或 GPT 指定 snapshot 不可用。
2. API key 缺失或认证失败。
3. Judge Prompt hash 变化。
4. smoke 质量门槛失败。
5. schema valid < 100%。
6. evidence 子串校验失败。
7. 去重后损失 > 2% 且定向 top-up 仍失败。
8. 调用预估超出授权规模 10% 以上。
9. 连续系统性 rate limit/server error，重试已无法合理恢复。

遇到单条暂时失败：指数退避、记录失败、继续其他格，最后 resume 补齐。不得吞掉错误或用空结果假装成功。

## 十四、执行顺序

严格按以下顺序，完成一阶段再进入下一阶段：

1. 仓库审计、记录 dirty 状态、冻结四个 Judge hash。
2. 实现六域通用框架。
3. 创建 C3/C4/C5/C6 域资产、Generator/Auditor Prompt 和计划。
4. 补齐测试，运行全部零网络测试。
5. dry-run。
6. 四域 smoke 生成、Auditor 和逐条抽查。
7. smoke 合格后执行四域 Gemini 五轮全量生成。
8. 四域独立 Auditor 全量审核和 review queue。
9. 用未修改 Judge Prompt 跑 GPT-5.4-mini high reasoning baseline。
10. 计算指标、生成四份 Excel 和四域汇总。
11. 重新验证 Judge hash、输出行数、manifest、xlsx 和全部测试。
12. 给出最终交付报告。

不要把第 7～10 步改成“给用户命令自行执行”；你需要实际执行并留下产物。

## 十五、最终回复格式

最终回复必须分成两部分：

### 直接执行

- 明确说完成、部分完成或阻断。
- 列出实际生成的四域候选数、Auditor 完成数、Judge 完成数。
- 列出 Gemini、Auditor、Judge 的实际模型和 Judge 配置。
- 给出四份 Excel、summary.md、metrics.json 的绝对路径链接。
- 给出测试命令和结果。
- 给出四个 Judge Prompt 前后 hash 一致性。
- 若有未完成项，列出具体 case 数和原始阻断原因，不能写模糊的“网络问题”。

### 深度交互

- 说明这些分数为何仍不是生产准确率。
- 列出最应该人工审核的错误簇和 review queue。
- 提出下一步最短路径：先把高风险/边界样本变成人工 Gold，再考虑 V2 Prompt；不要直接根据合成标签反复调参。

开始执行。先读文件和现有实现，输出一个简短阶段计划，然后直接推进。
```

---

## 使用提醒

这份 Prompt 已经给出五轮全量调用授权，但把 smoke 质量门槛放在授权之前。执行 AI 若只交付脚本、没有真实 candidates、Auditor run、Judge run 和 Excel，就不算完成。

# C3／C4／C5／C6 Mock 数据生成与 Judge 评测 Workflow

> 目标：把已经在 C1/C2 验证过的 Prompt Lab，扩展为可重复生成、审核、冻结、评测和回归比较 C3～C6 的单域数据工程。
>
> 本文是执行规格，不把 Mock 分数当线上准确率；人工 Gold 与真实脱敏评论仍是最终依据。

## 0. 先说结论：当前不能直接把 C2 命令换成 C3～C6

现有流水线的骨架可以复用，但代码只正式启用了 C1/C2：

- `schemas.py::DOMAIN_L2_CODES` 只有 C1/C2。
- `generate_cases.py::_GEN_PROMPTS` 只有 C1/C2。
- `audit_cases.py::_AUDIT_PROMPTS` 与 `auditor_contract()` 只有 C1/C2。
- `build_spec()` 把生成规则写成 `if C1 else C2`，C3～C6 会被误套 C2 的“交付物品质”语义。
- 现有 `contrast_pair` 只支持“A＝本域 true、B＝本域 false”；不能测 C3/C6 最关键的本域 L2 混淆。
- `evaluate_prompt.py` 的 schema name、CLI 描述和默认 Prompt 仍写死 C1。
- `report.py` 的 Markdown 标题仍写死 C1。
- Excel 报告目前是 C2 一次性构建脚本，不是通用 CLI。

因此正确顺序不是“先大量调用 Gemini”，而是：

```text
规则定策 → 工程泛化 → 小规模 smoke → 人工审题 → 单轮生成 → 冻结 Gold → Judge baseline → 扩量 → Prompt 调优
```

如果跳过前两步，得到的只是“格式正确的合成文本”，不是可验证 Judge 的数据集。

---

## 1. 第一性原理：一条可用 Mock 样本必须满足什么

每条样本必须同时满足四个条件：

1. **责任事实明确**：从文本本身能判断“谁应该修”。
2. **本域标签唯一可辩护**：不能靠 Generator 脑补页面、订单或现场事实。
3. **L2 可区分**：不仅判断是不是本域，还能区分本域内部近邻 L2。
4. **证据逐字落地**：正例 evidence 是评论中的连续原文，不是摘要。

因此覆盖计划必须包含两种最小对照：

- **跨域对照**：A 为本域 true，B 为本域 false，只改变责任站点。
- **本域 L2 对照**：A、B 都是本域 true，但分别落入不同 L2，只改变决定 L2 的事实。

C2 主要靠跨域对照即可暴露问题；C3/C4/C5/C6 若没有本域 L2 对照，L2 Exact 很容易虚高。

---

## 2. 四个域的标签目录

| 域 | L2 | 数量 |
|---|---|---:|
| C3 供应商履约 | C-3-1 人员服务；C-3-2 驾驶接送；C-3-3 带团节奏；C-3-4 约定履行；C-3-5 现场安全与卫生；C-3-6 风险应变与告知；C-3-7 不当行为 | 7 |
| C4 平台与系统 | C-4-1 开通启用；C-4-2 凭证与资格；C-4-3 平台功能 | 3 |
| C5 客服营运 | C-5-1 确认/修改；C-5-2 取消/退款；C-5-3 客服应对 | 3 |
| C6 理解期待 | C-6-1 个人因素；C-6-2 价值感落差；C-6-3 内容期待落差；C-6-4 天候与自然因素；C-6-5 外部突发事件；C-6-6 信息误读 | 6 |

Judge Prompt 的 schema 已验证为上述 7／3／3／6 个 code。

---

## 3. 生成前必须锁定的政策边界

这些不是 Generator 可以自行决定的文案细节，而是标签政策。应先写成 `policy_decisions`，由负责人确认后再生成。

| 冲突 | 建议决定性事实 |
|---|---|
| C3-4 vs C1 | 页面承诺清楚且供应商现场没做＝C3-4；页面描述本身错误、模糊或与实际规格不一致＝C1。若现有 Prompt 对“保证成团”仍有冲突，先人工定策。 |
| C3-5 vs C2 | 交付物一般品质/舒适问题＝C2；存在人身或健康风险、公共区域管理失当＝C3-5。 |
| C3-6 vs C6-4/C6-5 | 外力事件本身且已合理应变＝C6；商家对已知风险未告知、无备案或处置失当＝C3-6。两项事实同时存在时允许跨域并存。 |
| C4-1 vs C2-1 | 尚未成功开通＝C4-1；已成功开通后网速/连接品质差＝C2-1。 |
| C4-2 vs C1-5 vs C6-6 | 规则未写清＝C1-5；规则清楚但系统/资格仍卡住＝C4-2；规则清楚且旅客自承没看/选错＝C6-6。 |
| C4-3 vs C5-3 | App/网页功能、按钮、订单状态本身坏＝C4-3；客服或 AI 客服的回复时效、推诿、答非所问＝C5-3。 |
| C5-1 vs C1-7 | 页面未揭露退改/服务承诺＝C1-7；已经进入客服确认或改期流程，但政策设计/衔接没落实＝C5-1。 |
| C5-2 vs C5-3 | 退款结果被拒或款项延宕＝C5-2；客服回复慢、推诿或程序繁琐＝C5-3。可同时成立时最多输出两条。 |
| C6-1 vs C6-6 | 身体、迟到、自行改变计划、明确承认操作按错＝C6-1；信息本来写清，但没读、会错意或选错方案＝C6-6。 |
| C6-2 vs C6-3 | 明确以价格/费用/划算与否比较价值＝C6-2；不涉及价格，只是内容、规模、氛围不如想象＝C6-3。 |
| C6-4 vs C6-5 | 天候、天灾、自然条件＝C6-4；疫情、罢工、第三方运输、场馆关闭、公共管制＝C6-5。 |

### 规则锁定的验收方式

每条边界至少先人工写 3 组最小对照，共 6 条文本；负责人能稳定给出相同标签后，才能交给 Generator 扩写。

---

## 4. 目标工程结构：一个通用框架，四份域配置

不要复制四份 `build_c*_plans.py` 和四份批处理脚本。建议新增：

```text
evals/prompt_lab/
  domains/
    c3.json
    c4.json
    c5.json
    c6.json
  prompts/generators/
    c3_generator.md  c3_auditor.md
    c4_generator.md  c4_auditor.md
    c5_generator.md  c5_auditor.md
    c6_generator.md  c6_auditor.md
  plans/
    c3_layer1_plan.json  c3_layer2_plan.json
    c4_layer1_plan.json  c4_layer2_plan.json
    c5_layer1_plan.json  c5_layer2_plan.json
    c6_layer1_plan.json  c6_layer2_plan.json

scripts/prompt_lab/
  build_domain_plans.py
  generate_domain_gemini_mock.sh
  build_domain_eval_workbook.mjs
```

域配置是单一事实源，至少包含：

```json
{
  "domain": "C-3",
  "name": "供应商履约",
  "l2": [
    {"code": "C-3-1", "name": "人员服务", "positive_contract": "..."}
  ],
  "negative_domains": ["C-1", "C-2", "C-4", "C-5", "C-6"],
  "domain_boundaries": {
    "C-3-1": ["C-5-3", "C-2", "C-6-3"]
  },
  "l2_confusion_pairs": [
    ["C-3-1", "C-3-2"],
    ["C-3-3", "C-3-4"]
  ],
  "policy_decisions": ["..."]
}
```

### 必须泛化的代码

1. `schemas.py`
   - 加入 C3～C6 L2 code。
   - `DOMAIN_L2_CODES` 覆盖六域。
   - Auditor 输出改成按域动态生成 schema，不再为每域复制一个 Pydantic 类。
   - 新增 `l2_pair` 计划类型：A/B 都为 true，但目标 L2 不同。
2. `generate_cases.py`
   - Prompt 路由覆盖 C3～C6。
   - `build_spec()` 从域配置读取正例、负例、uncertain 和 pair 合约，删除 `if C1 else C2`。
3. `audit_cases.py`
   - Prompt 路由覆盖 C3～C6。
   - 独立问题字段统一为 `contains_independent_target_issue`；读取旧 C1/C2 数据时保留兼容层。
4. `metrics.py` / `report.py`
   - 增加 `l2_pair_both_correct_rate`。
   - 标题、域名、schema name 从数据或 Prompt 推导，移除 C1 硬编码。
5. `build_manifest.py`
   - `domains_under_test` 改为 C1～C6。
   - 加入 8 份新 Generator/Auditor Prompt 的 SHA-256。
6. 测试
   - 每域 Prompt schema enum 必须等于域配置 L2。
   - 每域 Candidate/Auditor/Plan 路由通过 fake client。
   - domain pair、l2 pair、resume、证据逐字、数量总和均有测试。

---

## 5. 覆盖计划与建议数量

### 5.1 Layer 1：规则单元与基础弃权

每个 L2 生成 10 条明确正例：

| 表达 | 数量 | 难度/倾向 |
|---|---:|---|
| direct | 3 | easy / negative |
| colloquial | 2 | medium / negative |
| euphemistic | 2 | medium / negative |
| rhetorical_question | 1 | hard / negative |
| noisy | 1 | medium / negative |
| neutral_mixed | 1 | hard / neutral |

另外每域固定：

- 其他 5 域负例：每域 10 条，共 50。
- 纯正向防御样本：10 条。

所以 Layer 1 数量为：

| 域 | L2 正例 | 他域负例 | 防御正向 | Layer 1 |
|---|---:|---:|---:|---:|
| C3 | 70 | 50 | 10 | 130 |
| C4 | 30 | 50 | 10 | 90 |
| C5 | 30 | 50 | 10 | 90 |
| C6 | 60 | 50 | 10 | 120 |

### 5.2 Layer 2：边界、混合、存疑、对抗

每个 L2 固定：

- 跨域最小对照：3 个边界 × 3 对 × 2 侧＝18 条。
- 混合评论：4 条。
- uncertain：4 条。
- 对抗表达：4 条。

基础 Layer 2 为每 L2 30 条。

### 5.3 本域 L2 最小对照（正式数据必须加）

每条 `l2_confusion_pair` 生成 3 对，共 6 条。建议边：

| 域 | 本域 L2 混淆边 |
|---|---|
| C3 | 3-1↔3-2；3-1↔3-7；3-3↔3-4；3-4↔3-6；3-5↔3-6；3-3↔3-7 |
| C4 | 4-1↔4-2；4-1↔4-3；4-2↔4-3 |
| C5 | 5-1↔5-2；5-1↔5-3；5-2↔5-3 |
| C6 | 6-1↔6-6；6-2↔6-3；6-4↔6-5 |

正式单轮总量：

| 域 | Layer 1 | 基础 Layer 2 | L2 对照 | 单轮总量 | 5 轮目标 |
|---|---:|---:|---:|---:|---:|
| C3 | 130 | 210 | 36 | 376 | 1,880 |
| C4 | 90 | 90 | 18 | 198 | 990 |
| C5 | 90 | 90 | 18 | 198 | 990 |
| C6 | 120 | 180 | 18 | 318 | 1,590 |
| 合计 | 430 | 570 | 90 | 1,090 | 5,450 |

不要一开始就跑 5 轮。先完成每域 20～40 条 smoke 和人工审题，再跑单轮；单轮通过后才扩到 5 轮。

---

## 6. 各域跨域边界矩阵

以下是生成计划的默认靶点；`policy_decisions` 的人工结论优先。

### 6.1 C3 供应商履约

| L2 | 三个跨域边界 |
|---|---|
| C-3-1 人员服务 | C-5-3 售后客服；C-2 交付物状态；C-6-3 主观期待 |
| C-3-2 驾驶接送 | C-2-3 车况；C-6-1 旅客自己迟到；C-4-2 接送/资格系统卡关 |
| C-3-3 带团节奏 | C-1-2 页面流程未写清；C-6-3 依表执行但主观嫌赶；C-6-4/5 外力延误 |
| C-3-4 约定履行 | C-1 页面承诺本身错误；C-4-2 核销失败；C-5-3 取消后的客服应对 |
| C-3-5 安全卫生 | C-2-2 餐饮区品质；C-2-3 车辆舒适；C-2-5 器材老旧但无风险 |
| C-3-6 风险应变 | C-6-4 天候且合理应变；C-6-5 外部事件且合理应变；C-5-2 事后退款结果 |
| C-3-7 不当行为 | C-6-2 单纯嫌贵；C-5-3 售后争执但无胁迫；no_issue 正常礼貌请求评价 |

### 6.2 C4 平台与系统

| L2 | 三个跨域边界 |
|---|---|
| C-4-1 开通启用 | C-2-1 已开通后的网络品质；C-1-5 页面没写操作；C-6-1 旅客自承设置错误 |
| C-4-2 凭证资格 | C-3-4 凭证根本未发/项目没给；C-1-5 兑换规则没写清；C-6-6 规则清楚但旅客没读 |
| C-4-3 平台功能 | C-5-3 客服回复品质；C-1-5 页面说明缺漏；C-6-1 个人装置或操作问题 |

### 6.3 C5 客服营运

| L2 | 三个跨域边界 |
|---|---|
| C-5-1 确认修改 | C-1-7 页面政策未揭露；C-4-3 修改功能故障；C-3-1 现场人员交接/态度 |
| C-5-2 取消退款 | C-1-7 退款规则未写清；C-6-4/5 外力但客服依政策正常处理；C-3-4 原始约定未履行但尚无退款争议 |
| C-5-3 客服应对 | C-3-1 当地现场人员；C-4-3 平台功能坏；C-6 外力/个人因素且客服正常说明 |

### 6.4 C6 理解期待

| L2 | 三个跨域边界 |
|---|---|
| C-6-1 个人因素 | C-3-2/3 供应商迟到；C-4-2 凭证真故障；C-1-5 页面缺操作说明 |
| C-6-2 价值落差 | C-1-3 隐藏费用；C-3-4 临时加价；C-2 具体品质瑕疵 |
| C-6-3 内容期待 | C-1 页面描述不符；C-2 具体品质瑕疵；C-3-3 执行偏离表定 |
| C-6-4 天候自然 | C-3-6 应变失当；C-1-6 风险未揭露；C-6-5 非天候外部事件（用于 L2 对照） |
| C-6-5 外部事件 | C-3-6 应变失当；C-1-6 风险未揭露；C-6-4 天候自然（用于 L2 对照） |
| C-6-6 信息误读 | C-1-5 信息本身不清；C-4-2 系统/资格真卡关；C-5 订单扣款/退款处理 |

---

## 7. Generator 与 Auditor Prompt 规范

每个域各有独立 Generator 与 Auditor Prompt；不得让 Generator 直接读取或改写 Judge 的示例。

### Generator 必须包含

- 本域一句话责任契约。
- 各 L2 的决定性事实与最低证据门槛。
- 他域负例说明。
- domain pair 与 l2 pair 的唯一变量要求。
- true/false/uncertain 的文本要求。
- 正例逐字证据、负例/uncertain 空证据。
- 禁止出现 C3/L2/Judge/标准答案等评测术语。

### Auditor 必须检查

- `label_supported`
- `ambiguous`
- `self_contained`
- `contains_independent_target_issue`
- `suggested_domain`
- `suggested_l2_codes`
- `evidence_quotes_valid`
- `near_duplicate`
- `audit_reason`

额外规则：

- domain pair 是否只改变责任站点。
- l2 pair 是否两侧都属于本域、只改变决定 L2 的事实。
- C3-5/C3-7 高风险样本必须检查严重度是否足够；不能把一般不舒适写成安全风险，也不能把一般态度差写成恶意行为。

---

## 8. 模型隔离与运行配置

建议角色分离：

| 角色 | 建议模型 | 目的 |
|---|---|---|
| Generator | `gemini-3.5-flash` | 跨模型出题，降低与 Judge 同源偏差 |
| Auditor | 独立 OpenAI snapshot，例如当前已用的 `gpt-5.5-2026-04-23` | 审标签，不使用 Judge Prompt |
| Judge | 实际被测模型，例如 `gpt-5.4-mini-2026-03-17` | 评测对象 |
| Human | 域专家/产品负责人 | 产生最终 Gold |

Judge 的公平比较必须固定：

```json
{
  "temperature": 1,
  "reasoning_effort": "high",
  "thinking": true
}
```

批量发现阶段每条跑 1 次；冻结 Dev/Holdout 后，关键集跑 3 次测稳定性。不要对全部 5,450 条直接 repeats=3，成本高且不能替代人工 Gold。

---

## 9. 完整端到端 Workflow

### Phase A：工程启用（零 API）

1. 新增域配置、Generator/Auditor Prompt。
2. 泛化 schema、Prompt 路由、计划类型和报告标题。
3. 生成计划与 Prompt manifest。
4. 运行全部 fake-client 测试。

目标命令（需先由工程任务实现）：

```bash
.venv-promptlab/bin/python scripts/prompt_lab/build_domain_plans.py --domain C-3
.venv-promptlab/bin/python scripts/prompt_lab/build_domain_plans.py --domain C-4
.venv-promptlab/bin/python scripts/prompt_lab/build_domain_plans.py --domain C-5
.venv-promptlab/bin/python scripts/prompt_lab/build_domain_plans.py --domain C-6
.venv-promptlab/bin/python scripts/prompt_lab/build_manifest.py
.venv-promptlab/bin/python -m pytest backend/tests/prompt_lab -q
```

验收：计划数量严格等于配置公式；四个 Judge schema enum 与域配置完全一致；零网络测试通过。

### Phase B：Smoke 生成（每域 20～40 条）

```bash
.venv-promptlab/bin/python scripts/prompt_lab/generate_cases.py \
  --plan evals/prompt_lab/plans/c3_layer1_plan.json \
  --provider gemini --model gemini-3.5-flash \
  --out tmp/prompt_lab/c3-smoke.jsonl --limit 8
```

对 C4/C5/C6 替换域名。先人工查看文本、证据和标签，不通过则改域配置/Generator Prompt，不改 Judge Prompt。

Smoke 验收：

- true 正例都有逐字证据。
- false 没有偷偷包含本域独立问题。
- uncertain 确实无法只靠文本判定，而不是单纯写得短。
- pair 两侧场景一致，只改变一个责任事实。
- 不出现模板化重复和 Judge 术语。

### Phase C：单轮生成

```bash
CONFIRM_COST=1 DOMAIN=C-3 ROUNDS=1 WORKERS=4 \
  OUT_DIR=tmp/prompt_lab/c3-gemini35-round1 \
  bash scripts/prompt_lab/generate_domain_gemini_mock.sh
```

对 C4/C5/C6 分别执行。脚本必须支持 `DRY_RUN=1`、`--resume`、跨轮 case ID 前缀、文本去重和 generation manifest。

### Phase D：独立 Auditor

```bash
.venv-promptlab/bin/python scripts/prompt_lab/audit_cases.py \
  --input tmp/prompt_lab/c3-gemini35-round1/c3-all-candidates.jsonl \
  --model "$PROMPT_LAB_AUDITOR_MODEL" \
  --out tmp/prompt_lab/c3-gemini35-round1/c3-all-audits.jsonl \
  --review-queue tmp/prompt_lab/c3-gemini35-round1/c3-review.csv \
  --workers 8 --resume --all --confirm-cost
```

必须进入人工队列：

- 所有 uncertain。
- 所有 domain pair 与 l2 pair。
- 所有 auditor `review_required`。
- 其余 accepted 分层抽 20%。
- C3-5、C3-7 全量人工审核。

### Phase E：人工修订并冻结 Dev/Holdout

人工在 review CSV 填：`accept | edit | reject`。对 edit 必须保留新文本、新证据、新标签和修改原因。

```bash
.venv-promptlab/bin/python scripts/prompt_lab/build_dataset.py \
  --candidates tmp/prompt_lab/c3-gemini35-round1/c3-all-candidates.jsonl \
  --audits tmp/prompt_lab/c3-gemini35-round1/c3-all-audits.jsonl \
  --human-review tmp/prompt_lab/c3-gemini35-round1/c3-review.csv \
  --dataset-version c3-v1 \
  --out-dir evals/prompt_lab/datasets/c3 --split-seed 42
```

冻结规则：

- 70% Dev / 30% Holdout。
- 同一 domain pair 或 l2 pair 不得跨 split。
- 文本及近重复不得跨 split。
- Holdout 在 Prompt 调优期间不可查看逐条答案。
- 每次修改产生新 dataset version 与 SHA-256，禁止覆盖。

### Phase F：跑 Judge Baseline

```bash
.venv-promptlab/bin/python scripts/prompt_lab/evaluate_prompt.py \
  --prompt evals/prompt_lab/prompts/judges/03_C-3_supplier.md \
  --dataset evals/prompt_lab/datasets/c3/c3-v1-dev.jsonl \
  --model gpt-5.4-mini-2026-03-17 \
  --temperature 1 --reasoning-effort high --thinking \
  --repeats 1 --out tmp/prompt_lab/runs/c3-v1-dev-high \
  --workers 8 --no-cache --resume --all --confirm-cost
```

Judge Prompt 映射：

```text
C3 → 03_C-3_supplier.md
C4 → 04_C-4_platform.md
C5 → 05_C-5_service.md
C6 → 06_C-6_customer.md
```

### Phase G：生成 Excel 完整报告

```bash
node scripts/prompt_lab/build_domain_eval_workbook.mjs \
  --domain C-3 \
  --candidates evals/prompt_lab/datasets/c3/c3-v1-dev.jsonl \
  --audits tmp/prompt_lab/c3-gemini35-round1/c3-all-audits.jsonl \
  --run tmp/prompt_lab/runs/c3-v1-dev-high \
  --out outputs/c3-report/C3_判官完整除错报告.xlsx
```

Excel 固定四表：

1. 总览。
2. 符合（应命中本域）。
3. 不符合（应弃权）。
4. 边界与存疑。

固定列：标准标签/L2/逐字证据、Judge 命中/L2/证据/信心、对错、Auditor 建议、人工判定、人工备注。

### Phase H：Prompt 调优与严格回归

1. 保留原始 Judge 为 baseline，不覆盖。
2. 只根据错误簇修改候选 Prompt。
3. 同模型、同配置、同 Dev 数据重跑。
4. 用 `compare_runs.py` 做 fixed/regressed/unchanged_wrong。
5. 候选通过 Dev 后，只运行一次 Holdout。

```bash
.venv-promptlab/bin/python scripts/prompt_lab/compare_runs.py \
  --baseline tmp/prompt_lab/runs/c3-v1-dev-high \
  --candidate tmp/prompt_lab/runs/c3-v2-dev-high \
  --out tmp/prompt_lab/comparisons/c3-v1-vs-v2
```

---

## 10. 评测指标与晋级门槛

通用门槛：

| 指标 | 门槛 |
|---|---:|
| Schema valid | 100% |
| 逐字证据落地 | 100% |
| Traceability | 100% |
| Layer 1 Recall | ≥95% |
| Layer 1 Specificity | ≥95% |
| L2 Exact | ≥90% |
| Layer 2 Precision / Recall | 各 ≥90% |
| domain pair 两侧全对 | ≥85% |
| l2 pair 两侧 L2 全对 | ≥85% |
| 最大边界 FPR | ≤15% |
| uncertain 强制归因 | ≤30% |
| 新增 Layer 1 回退 | 不得下降超过 1pp |

C3 额外门槛：

- C3-5 与 C3-7 Recall 各 ≥95%。
- 高风险误报/漏报必须逐条人工确认，不能只看宏平均。

只有以下条件同时满足才晋级：

```text
目标错误簇改善
AND Layer 1 无显著回退
AND 非目标边界没有新增尖峰
AND Holdout 达标
AND 所有新增错误已进入人工清单
```

---

## 11. 扩量策略

单轮通过后再扩到 5 轮：

```bash
CONFIRM_COST=1 DOMAIN=C-3 ROUNDS=5 WORKERS=4 \
  OUT_DIR=tmp/prompt_lab/c3-gemini35-5rounds \
  bash scripts/prompt_lab/generate_domain_gemini_mock.sh
```

扩量不是简单重复同一计划。每轮至少轮换：

- 商品类型与地点。
- 语言/繁简/中英混合。
- 评论长度。
- 情绪表达。
- 对抗技巧。
- 边界事实的具体载体。

跨轮必须 NFKC + 空白正规化去重，并报告：请求量、成功量、重复丢弃量、失败格、各 L2/边界分布。

---

## 12. 可直接交给其他 AI 的任务包

这些任务应依序合并。AI-B/C/D 不应同时修改 `schemas.py`；共享框架由 AI-A 先完成。

### AI-A：Prompt Lab 六域泛化

```text
目标：把现有只支持 C1/C2 的 Prompt Lab 泛化到配置驱动的 C1～C6，不调用真实 API。

必须完成：
1. 新增 evals/prompt_lab/domains 的域配置 schema 与 loader。
2. schemas.py 支持 C3～C6 L2，并把 Auditor schema 改成动态按域生成。
3. generate_cases.py / audit_cases.py 改为配置驱动 Prompt 路由。
4. 新增 domain_pair 与 l2_pair；l2_pair 两侧都 expected_domain=true、目标 L2 不同。
5. metrics/report 增加 l2_pair_both_correct_rate，移除 C1 标题/schema_name 硬编码。
6. build_manifest.py 覆盖六域。
7. 补 fake-client 与 schema/plan/metric 测试；现有 C1/C2 测试不得回退。

约束：不改 Judge Prompt；不调用 API；不触碰生产 backend；所有现有 JSONL 保持向后兼容。
验收：pytest 全过；C1/C2 计划重建 hash/数量保持不变；C3～C6 空壳配置能通过 plan/schema dry-run。
```

### AI-B：C3 域资产

```text
依 docs/PRD-C3-C6-MOCK-DATA-WORKFLOW.md，为 C3 创建：
- domains/c3.json
- c3_generator.md
- c3_auditor.md
- c3 layer1/layer2 plans
- C3 fake-client、边界矩阵、数量与 Judge schema 对齐测试

重点：C3-4 vs C1、C3-5 vs C2、C3-6 vs C6、C3-7 vs 一般态度/购物站/加价必须写成最小对照；C3-5/C3-7 全量人工审核。
不得调用真实 API，不得修改 03_C-3_supplier.md。
```

### AI-C：C4 与 C5 域资产

```text
依 workflow 为 C4/C5 分别创建域配置、Generator、Auditor、两层计划与测试。

C4 重点：启用前后、凭证未发 vs 核销失败、页面规则不清 vs 系统卡关 vs 用户没读、平台功能 vs 客服互动。
C5 重点：政策/确认、退款结果、客服过程三者的本域 L2 pair；现场人员必须归 C3，平台功能必须归 C4。
不得调用真实 API，不得修改 Judge Prompt。
```

### AI-D：C6 域资产

```text
依 workflow 为 C6 创建域配置、Generator、Auditor、两层计划与测试。

必须覆盖三组本域 L2 pair：C6-1 vs C6-6、C6-2 vs C6-3、C6-4 vs C6-5。
跨域重点：页面没写清→C1、具体品质→C2、执行偏离/应变失当→C3、系统资格卡关→C4、客服处理失当→C5。
不得把“很差/很雷/不值”单独当明确正例；uncertain 必须真的缺责任事实。
不得调用真实 API，不得修改 Judge Prompt。
```

### AI-E：批处理与 Excel 报告

```text
在六域框架和域资产合并后，实现：
1. generate_domain_gemini_mock.sh：DOMAIN/ROUNDS/WORKERS/OUT_DIR/DRY_RUN/CONFIRM_COST，支持 resume、跨轮去重、manifest。
2. build_domain_eval_workbook.mjs：通用四工作表报告，红底错误、黄底抽样、人工判定下拉。
3. 对每张工作表 render + inspect；扫描公式错误；导出 xlsx 后重新读取验证。
4. 更新 README 的 C3～C6 命令。

不得硬编码某一域的 L2 名称、数量或标题，全部读取域配置和 run manifest。
```

### AI-F：Smoke 与人工审题协调

```text
在 API key 已配置且工程测试全部通过后：
1. 每域先 dry-run，再只跑 8 个生成格。
2. 生成 smoke candidates 与 audits，不运行 Judge。
3. 输出逐条人工审题表，重点检查责任事实、L2、证据、pair 唯一变量。
4. 不合格时只提出 Generator/Auditor/域配置修订，不修改 Judge Prompt。
5. 人工确认后才申请单轮全量成本授权。
```

---

## 13. 总验收清单

- [ ] C3～C6 Judge schema 与域配置 L2 完全一致。
- [ ] Generator/Auditor/Judge 三角色 Prompt 隔离。
- [ ] domain pair 与 l2 pair 都可生成、审核、冻结和计分。
- [ ] 所有正例 evidence 为逐字子串。
- [ ] false 不含独立成立的本域问题。
- [ ] uncertain 不被伪装成“短文本明确正例”。
- [ ] 对照对不跨 Dev/Holdout。
- [ ] 每个输出有 Prompt SHA、模型、请求 ID、运行配置与数据 SHA。
- [ ] 批量脚本有 dry-run、费用确认、resume 与去重。
- [ ] Excel 有四表、人工栏、红黄标色并完成视觉检查。
- [ ] baseline 在改 Prompt 前完成并冻结。
- [ ] 候选 Prompt 用相同模型和配置做严格 A/B。
- [ ] Holdout 只在候选定稿后运行一次。
- [ ] 最终上线判断使用真实脱敏 Gold，不使用 Mock 分数替代。

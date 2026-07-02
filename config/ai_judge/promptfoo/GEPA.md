# GEPA 自動優化歸因指令（DSPy + promptfoo-metric）

用 GEPA（DSPy 3.x 的反射式演化優化器，ICLR 2026）自動優化歸因**系統指令**，metric 用各 L3 的 positive_cases 當 trainset。harness＝`scripts/optimize_attribution_gepa.py`。**分階段**（對齊 cascade 判決引擎）：

| `--stage` | 優化對象 | 回填目標 | 輸出檔 |
|---|---|---|---|
| `a` | Stage A 域分類指令（注入 `global_rule.decision_tree`，只判 6 域） | `prejudge._stage_a_system` 核心措辭 | `gepa_optimized_stage_a.txt` |
| `b` | Stage B 單域 L2/L3 指令（僅注入選中域目錄） | `prejudge._attr_system`（Stage B 用） | `gepa_optimized_stage_b.txt` |
| `legacy`（預設） | 單次全目錄 Stage2 指令 | `prejudge._ATTR_SYS` | `gepa_optimized_instruction.txt` |

## 定位（誠實邊界）
- **優化對象＝各階段「指令」**（怎麼依決策樹/目錄選），**不是逐條改寫 canon**。canon 是注入 prompt 的資料。
- **canon 有間接進來**：metric 的 feedback **注入 gold canon（stage b/legacy）或域 core（stage a）**——反射 LM 據此學邊界，優化出的指令吸收判準知識。label-free 下把「嚴格邊界」餵進優化的關鍵。
- **要連 canon 一起演化**：GEPA `instruction_proposer` 自訂鉤子可把 canon block 當可優化元件逐條演化（進階，見文末）。

## 用法
```bash
# 0) 已裝 dspy（backend/.venv）。需一組真 token 的 user（或設 PROMPTFOO_USER_ID）。
# 1) 驗接線（3 條，便宜）——選階段
backend/.venv/bin/python scripts/optimize_attribution_gepa.py --stage a --smoke
backend/.venv/bin/python scripts/optimize_attribution_gepa.py --stage b --smoke
# 2) 真優化（有 token 成本；GEPA 打大量 LLM）；反射建議用較強模型
backend/.venv/bin/python scripts/optimize_attribution_gepa.py --stage a --budget light --reflection-model gpt-5.4
backend/.venv/bin/python scripts/optimize_attribution_gepa.py --stage b --budget light --reflection-model gpt-5.4
# 3) 人審 gepa_optimized_stage_{a,b}.txt → 回填 prejudge（見上表）→ 開 cascade.enabled=true 跑 promptfoo 驗不退步。
```

## ⚠️ 成本
GEPA 即使 `auto=light` 也會打**數百次** LLM（rollout + 反射）。trainset＝C-2~C-6 positive_cases（排除 content C-1——其 positive_cases 是合規商品名非投訴）。建議判準大改後或要系統性提升準確度時才跑，非每次 commit。

## metric（feedback-shaped）
- **stage a**：exact 域＝1.0 / 否則 0；feedback 含 gold 域 core → 導引先辨識主體域。
- **stage b / legacy**：exact code＝1.0 / 同 L2(b)或同域(legacy)＝0.3 / 完全錯＝0.0；feedback 含 gold `l1›l2›l3` + canon 摘要 + 診斷。

## 分階段 + 驗證流程（呼應 cascade 重構）
1. `--stage a` 優化域分類指令 → 回填 `_stage_a_system`。
2. `--stage b` 優化單域細分指令 → 回填 `_attr_system`。
3. **promptfoo（同目錄）量化驗證**：`global_rule.cascade.enabled=true` 後，`npx promptfoo eval` 經 `provider.py`→`to_finding`（自動走 cascade 路徑）比優化前後命中率，**不退步才採用**。

## 進階：連 canon 一起演化
`dspy.GEPA(instruction_proposer=...)` 傳自訂 proposer，把每域 canon block 建成獨立可優化元件（`component_selector`），讓 GEPA 反射直接改寫 canon 文字。需把 `prejudge._l2_canon_block` / `_l3_catalog` 重構成 DSPy 可尋址的 named component；工程量較大，待指令層優化見效後再評估。

> 其他成熟輪子：純 label-free 可另評 Meta Prompt Duel Optimizer（pairwise，無真值）；rubric 精煉可評 Evidently。見對話中調研。

# GEPA 自動優化歸因指令（DSPy + promptfoo-metric）

用 GEPA（DSPy 3.x 的反射式演化優化器，ICLR 2026）自動優化 Stage2 歸因的**系統指令**，metric 用各 L3 的 positive_cases 當 trainset。harness＝`scripts/optimize_attribution_gepa.py`。

## 定位（誠實邊界）
- **優化對象＝Stage2 歸因「指令」**（`prejudge._ATTR_SYS` 那段：怎麼依 L3 目錄選 code），**不是逐條改寫 33 個 canon**。canon 是注入 prompt 的資料。
- **canon 有間接進來**：metric 的 feedback **注入 gold L3 的 canon**——GEPA 的反射 LM 據此學會「這條的邊界該怎麼判」，優化出的指令因此吸收了判準知識。這是 label-free 下把「嚴格邊界」餵進優化的關鍵。
- **要連 canon 一起演化**：GEPA 支援 `instruction_proposer` 自訂鉤子，可把 canon block 當可優化元件逐條演化（進階；harness 目前優化單一指令元件，擴充見文末）。

## 用法
```bash
# 0) 已裝 dspy 3.2.1（backend/.venv）。需一組真 token 的 user（或設 PROMPTFOO_USER_ID）。
# 1) 驗接線（3 條，便宜）
backend/.venv/bin/python scripts/optimize_attribution_gepa.py --smoke
# 2) 真優化（有 token 成本；GEPA 打大量 LLM）
backend/.venv/bin/python scripts/optimize_attribution_gepa.py --budget light      # light/medium/heavy
#    建議反射用較強模型：--reflection-model gpt-5.4
# 3) 回填：優化後指令存 config/ai_judge/promptfoo/gepa_optimized_instruction.txt
#          人審後覆蓋 backend/app/judge/prejudge.py 的 _ATTR_SYS。
```

## ⚠️ 成本
GEPA 即使 `auto=light` 也會打**數百次** LLM（rollout + 反射）。有 token 成本，非免費。trainset 現 102 條（C-2~C-6 positive_cases，排除 content C-1——其 positive_cases 是合規商品名非投訴）。建議判準大改後或要系統性提升準確度時才跑，非每次 commit。

## metric（feedback-shaped）
- exact code＝1.0 / 同 L1 域不同細項＝0.3 / 完全錯＝0.0
- feedback 文字含 gold 的 `l1›l2›l3` + canon 摘要 + 「域對細項錯 / 連域都錯」診斷 → 導引反射 LM 針對性改進指令。

## 兩段式建議（呼應調研）
1. **GEPA 優化指令**（本 harness）→ 提升分類準確度，指令回填 `_ATTR_SYS`。
2. **promptfoo（同目錄）量化驗證** → 優化前後跑 215/102 測試比命中率，確認沒退步。

## 進階：連 canon 一起演化
`dspy.GEPA(instruction_proposer=...)` 傳自訂 proposer，把每域 canon block 建成獨立可優化元件（`component_selector`），讓 GEPA 反射直接改寫 canon 文字。需把 `prejudge._l2_canon_block` / `_l3_catalog` 重構成 DSPy 可尋址的 named component；工程量較大，待指令層優化見效後再評估。

> 其他成熟輪子：純 label-free 可另評 Meta Prompt Duel Optimizer（pairwise，無真值）；rubric 精煉可評 Evidently。見對話中調研。

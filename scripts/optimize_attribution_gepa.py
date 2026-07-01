#!/usr/bin/env python3
"""GEPA 自動優化 Stage2 歸因「系統指令」（promptfoo positive_cases 當 trainset，反饋 metric）。

定位（誠實）：
- 這優化的是 Stage2 歸因**指令**（讓 LLM 更會依 L3 目錄選 code），**不是逐條改寫 canon**。
  優化後的指令可回填 backend/app/judge/prejudge.py 的 `_ATTR_SYS`。
- metric 的 feedback **注入 gold L3 的 canon**——讓 GEPA 反射 LM 用判準知識改進指令，
  是把「canon 導向的嚴格邊界」間接餵進優化迴圈的關鍵。
- 要「連 canon 一起演化」需 GEPA 的 instruction_proposer 自訂鉤子把 canon block 當可優化元件（進階，見 README）。

trainset：config/ai_judge/rule_C-2~C-6.json 的 positive_cases（真實評論句→期望 L3 code）；**排除 content C-1**
（其 positive_cases 是合規商品名範例，非評論投訴，不是歸因輸入）。

⚠️ 成本：GEPA 會打大量 LLM（即使 auto=light 也數百次），有 token 成本。預設 --smoke 只驗證接線不真優化。

用法：
  python scripts/optimize_attribution_gepa.py --smoke            # 只驗證接線（極少呼叫）
  python scripts/optimize_attribution_gepa.py --budget light     # 真優化（有 token 成本）
  PROMPTFOO_USER_ID=<uid> ... （指定取 token 的 user；預設第一個有 token 者）
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(_ROOT, "backend"))
_AIJUDGE = os.path.join(_ROOT, "config", "ai_judge")
_OUT = os.path.join(_AIJUDGE, "promptfoo", "gepa_optimized_instruction.txt")


def _effective_llm():
    """取一組真 token 的 effective LLM dict（model/base_url/provider_tokens）。"""
    from sqlalchemy import select

    from app.core import settings as app_settings
    from app.core import tables as T

    uid = os.environ.get("PROMPTFOO_USER_ID")
    if not uid:
        with T.get_engine().connect() as c:
            for r in c.execute(select(T.user_settings.c.user_id, T.user_settings.c.data)).mappings():
                d = json.loads(r["data"] or "{}")
                if any((d.get("provider_tokens") or {}).values()):
                    uid = r["user_id"]
                    break
    if not uid:
        sys.exit("找不到有 provider_token 的 user；先在設定面板填 token 或設 PROMPTFOO_USER_ID")
    return app_settings.effective_llm_dict(app_settings.load_settings(uid))


def _dspy_lm(dspy, eff, model=None, reasoning=None):
    """由 effective dict 組 dspy.LM（LiteLLM openai/ 前綴 + 自訂 base_url/token）。

    reasoning：gpt-5 系列的 reasoning_effort（minimal/low/medium/high）。分類 student 設 minimal
    大幅降延遲（~4s→~1s/call）；reflection 留較高以保指令生成品質。
    """
    from app.core import settings as app_settings

    base = (eff.get("base_url") or "").strip()
    provider = app_settings.provider_id_for(base)
    token = (eff.get("provider_tokens") or {}).get(provider) or ""
    m = model or eff.get("model") or "gpt-5-nano"
    is_reasoning = m.startswith(("gpt-5", "o1", "o3", "o4"))
    if is_reasoning:
        # gpt-5 / o 系列（reasoning）dspy 要求 temperature=1.0 + max_tokens>=16000
        kw = {"model": f"openai/{m}", "api_key": token, "temperature": 1.0, "max_tokens": 16000}
        if reasoning:
            kw["reasoning_effort"] = reasoning
    else:
        # 非推理（gpt-4.1-mini / 4o-mini）：正常參數，快且分類 temp=0 穩定
        kw = {"model": f"openai/{m}", "api_key": token, "temperature": 0.0, "max_tokens": 2000}
    if base:
        kw["api_base"] = base
    return dspy.LM(**kw)


def _candidate_catalog() -> str:
    """selectable 域全 L3 精簡目錄（code | 域›面向›細項 | 意義），與 prejudge Stage2 同款注入。"""
    from app.core import ai_judge

    domains = ai_judge.selectable_domains()
    lines = []
    for n in ai_judge.l3_nodes_for_domains([d["code"] for d in domains]):
        lines.append(f"{n['code']} | {n['l1_label']}›{n['l2_label']}›{n['l3_label']} | {(n.get('meaning') or '')[:40]}")
    return "\n".join(lines)


def _iter_leaves(node):
    """遞迴取葉節點（無 children，變深度：葉可在 L1/L2/L3）。"""
    kids = node.get("children")
    if not kids:
        yield node
        return
    for k in kids:
        yield from _iter_leaves(k)


def _build_trainset(dspy, catalog: str):
    """rule_C-2~C-6 各葉節點的 positive_cases → dspy.Example（排除 content C-1，變深度相容）。

    catalog 為常數輸入欄，必須列入 with_inputs——否則 GEPA 內部 Evaluate 不會傳給 program，
    模型看不到 L3 目錄（曾踩此坑：Missing ['catalog'] → 分數全爛）。
    """
    examples = []
    for f in sorted(glob.glob(os.path.join(_AIJUDGE, "rule_C-*.json"))):
        if f.endswith("rule_C-1.json"):
            continue  # content：positive_cases 是合規商品名，非評論投訴
        data = json.load(open(f, encoding="utf-8"))
        for l1 in data["tree"]:
            for leaf in _iter_leaves(l1):
                for pc in leaf.get("positive_cases", []):
                    if pc and len(pc) >= 6:
                        examples.append(
                            dspy.Example(review=pc, catalog=catalog, l3_code=leaf["code"]).with_inputs(
                                "review", "catalog"
                            )
                        )
    return examples


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true", help="只驗證接線（不真跑 GEPA）")
    ap.add_argument("--budget", choices=["light", "medium", "heavy"], help="真優化預算（auto）")
    ap.add_argument("--max-calls", type=int, default=None, help="硬上限 metric 呼叫數（取代 auto，早停快出：GEPA 增益前段集中）")
    ap.add_argument("--reflection-model", default=None, help="反射 LM 模型（建議用較強者，如 gpt-5.4）")
    ap.add_argument("--model", default=None, help="student 分類模型（預設 user 設定；快跑指定 gpt-4.1-mini 非推理）")
    ap.add_argument("--threads", type=int, default=16, help="並行 API 呼叫數（I/O bound，越高越快，受 provider RPM 限制）")
    ap.add_argument("--reasoning", default="minimal", help="student 分類的 reasoning_effort（minimal 最快，適合挑 code 任務）")
    args = ap.parse_args()

    try:
        import dspy
    except ImportError:
        sys.exit("dspy 未裝：backend/.venv/bin/pip install dspy")

    from app.core import ai_judge

    eff = _effective_llm()
    catalog = _candidate_catalog()
    valid = ai_judge.valid_l3_codes()

    # ── DSPy Signature：GEPA 演化這段 instruction docstring ──
    class AttributeReview(dspy.Signature):
        """依『問題分類 L3 目錄』把一則負向旅遊商品評論歸到最貼切的一條 L3，回其 code。
        只能從目錄內選 code；嚴格依細項邊界，無法明確歸類時回空字串（寧缺勿濫）。"""

        review: str = dspy.InputField(desc="旅客評論原文")
        catalog: str = dspy.InputField(desc="L3 目錄：code | 域›面向›細項 | 意義")
        l3_code: str = dspy.OutputField(desc="最貼切的一條 L3 code，或空字串")

    program = dspy.Predict(AttributeReview)

    def metric(gold, pred, trace=None, pred_name=None, pred_trace=None):
        """反饋式 metric：exact=1.0 / 同域=0.3 / 否則 0；feedback 注入 gold canon 供反射學邊界。"""
        got = (getattr(pred, "l3_code", "") or "").strip()
        want = gold.l3_code
        gnode = ai_judge.l3_by_code(want) or {}
        if got == want:
            return dspy.Prediction(score=1.0, feedback="正確歸類。")
        got_node = ai_judge.l3_by_code(got) if got in valid else {}
        same_dom = bool(got_node) and got_node.get("l1_domain") == gnode.get("l1_domain")
        score = 0.3 if same_dom else 0.0
        canon = (gnode.get("canon") or "")[:220]
        fb = (
            f"應歸 {want}（{gnode.get('l1_label','')}›{gnode.get('l2_label','')}›{gnode.get('l3_label','')}）。"
            f"其判準：{canon} 實際判「{got or '空'}」，"
            f"{'域對但細項錯——注意兄弟細項的邊界差異' if same_dom else '連歸因域都錯——先辨識評論主體屬哪一域'}。"
        )
        return dspy.Prediction(score=score, feedback=fb)

    trainset = _build_trainset(dspy, catalog)
    valset = trainset[::5]  # 每 5 筆抽 1 (~20%) 進 valset，防 GEPA overfit（確定性分割）
    trainpart = [e for i, e in enumerate(trainset) if i % 5 != 0]
    print(f"trainset：{len(trainpart)} train / {len(valset)} val（C-2~C-6 positive_cases，排除 content）", flush=True)

    dspy.configure(lm=_dspy_lm(dspy, eff, model=args.model, reasoning=args.reasoning))  # student（快跑指定 gpt-4.1-mini）

    if args.smoke or (not args.budget and not args.max_calls):
        # 只跑 3 條驗證 program + metric 接線，不動 GEPA
        sample = trainset[:3]
        ok = 0
        for ex in sample:
            pred = program(review=ex.review, catalog=catalog)
            r = metric(ex, pred)
            ok += r.score == 1.0
            print(f"  [{ex.l3_code}] 判→ {getattr(pred,'l3_code','?')} score={r.score}")
        print(f"smoke OK：{ok}/{len(sample)} exact。真優化請加 --budget light（有 token 成本）")
        return

    reflection = _dspy_lm(dspy, eff, model=args.reflection_model or args.model or eff.get("model"))
    gepa_kw = {"metric": metric, "reflection_lm": reflection, "num_threads": args.threads, "track_stats": True}
    if args.max_calls:
        gepa_kw["max_metric_calls"] = args.max_calls
    else:
        gepa_kw["auto"] = args.budget
    optimizer = dspy.GEPA(**gepa_kw)
    _b = f"max_calls={args.max_calls}" if args.max_calls else f"budget={args.budget}"
    print(f"GEPA 優化中（{_b}, threads={args.threads}, model={args.model or 'default'}）", flush=True)
    optimized = optimizer.compile(program, trainset=trainpart, valset=valset)

    # 存優化後指令
    instr = optimized.signature.instructions if hasattr(optimized, "signature") else str(optimized)
    os.makedirs(os.path.dirname(_OUT), exist_ok=True)
    open(_OUT, "w", encoding="utf-8").write(instr)
    print(f"\n=== 優化後指令 → {_OUT} ===\n{instr}\n")
    print("回填：把此指令覆蓋 backend/app/judge/prejudge.py 的 _ATTR_SYS（人審後）。")


if __name__ == "__main__":
    main()

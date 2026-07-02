#!/usr/bin/env python3
"""GEPA 自動優化歸因「系統指令」（promptfoo positive_cases 當 trainset，反饋 metric）。

分階段（對齊 cascade 判決引擎，見 prejudge / global_rule）：
- `--stage a`：優化 **Stage A 域分類**指令（決策樹注入 → 只判 6 域）；回填 `prejudge._stage_a_system` 核心措辭。
- `--stage b`：優化 **Stage B 單域 L2/L3**指令（僅注入選中域目錄）；回填 `prejudge._attr_system`（Stage B 用）。
- `--stage legacy`（預設，向後相容）：優化現行**單次全目錄** Stage2 指令；回填 `prejudge._ATTR_SYS`。

定位（誠實）：優化的是各階段**指令**（讓 LLM 更會依目錄/決策樹選），非逐條改寫 canon；metric 的 feedback
注入 gold canon / 域 core，讓反射 LM 用判準知識改進指令。連 canon 一起演化需 instruction_proposer（進階，見 README）。

trainset：`config/ai_judge/rule_C-2~C-6.json` 的 positive_cases（真實評論句）；**排除 content C-1**
（其 positive_cases 是合規商品名，非評論投訴）。Stage A gold＝該域機器值；Stage B / legacy gold＝L3 code。

⚠️ 成本：GEPA 會打大量 LLM（即使 auto=light 也數百次），有 token 成本。預設 --smoke 只驗證接線不真優化。

用法：
  python scripts/optimize_attribution_gepa.py --stage b --smoke          # 只驗接線
  python scripts/optimize_attribution_gepa.py --stage a --budget light   # 真優化 Stage A（有 token 成本）
  python scripts/optimize_attribution_gepa.py --stage b --budget light --reflection-model gpt-5.4
  PROMPTFOO_USER_ID=<uid> ...（指定取 token 的 user；預設第一個有 token 者）
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
_OUT_FILE = {
    "legacy": os.path.join(_AIJUDGE, "promptfoo", "gepa_optimized_instruction.txt"),
    "a": os.path.join(_AIJUDGE, "promptfoo", "gepa_optimized_stage_a.txt"),
    "b": os.path.join(_AIJUDGE, "promptfoo", "gepa_optimized_stage_b.txt"),
}


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


def _iter_leaves(node):
    """遞迴取葉節點（無 children，變深度：葉可在 L1/L2/L3）。"""
    kids = node.get("children")
    if not kids:
        yield node
        return
    for k in kids:
        yield from _iter_leaves(k)


def _catalog_for_domains(domain_codes: list[str]) -> str:
    """指定域（機器值清單）的 L3 精簡目錄（code | 域›面向›細項 | 意義）；空清單＝全 selectable 域。"""
    from app.core import ai_judge

    if not domain_codes:
        domain_codes = [d["code"] for d in ai_judge.selectable_domains()]
    lines = []
    for n in ai_judge.l3_nodes_for_domains(domain_codes):
        lines.append(f"{n['code']} | {n['l1_label']}›{n['l2_label']}›{n['l3_label']} | {(n.get('meaning') or '')[:40]}")
    return "\n".join(lines)


def _decision_tree_text() -> str:
    """Stage A 決策樹注入文字（各域 core + 所需證據 + 跨域界線）；讀 global_rule（DB active / seed fallback）。"""
    from app.core import global_rule

    dt = global_rule.decision_tree()
    gates = "\n".join(
        f"- {g.get('domain', '')}：{g.get('core', '')}（需證據：{g.get('need', '')}）"
        for g in dt.get("gates", [])
    )
    bounds = "\n".join(f"- {b}" for b in global_rule.global_boundaries())
    return f"六域決策樹（依優先序）：\n{gates}\n跨域界線：\n{bounds}"


def _leaf_examples():
    """走訪 rule_C-2~C-6 各葉節點，yield (domain_machine, leaf_code, positive_case)。排除 content C-1。"""
    for f in sorted(glob.glob(os.path.join(_AIJUDGE, "rule_C-*.json"))):
        if f.endswith("rule_C-1.json"):
            continue  # content：positive_cases 是合規商品名，非評論投訴
        data = json.load(open(f, encoding="utf-8"))
        for l1 in data["tree"]:
            domain = l1.get("domain", "")
            for leaf in _iter_leaves(l1):
                for pc in leaf.get("positive_cases", []):
                    if pc and len(pc) >= 6:
                        yield domain, leaf["code"], pc


# ── 分階段：各回 (Signature, trainset builder, metric, out_file, input 欄位) ────────────
def _build(stage: str, dspy, ai_judge):
    """依 stage 組 (program, metric, trainset, out_file, input_names)。"""
    valid = ai_judge.valid_l3_codes()

    if stage == "a":
        dt_text = _decision_tree_text()
        domain_core = {}
        try:
            from app.core import global_rule

            domain_core = {g.get("domain", ""): g.get("core", "") for g in global_rule.decision_tree().get("gates", [])}
        except Exception:  # noqa: BLE001  feedback 用，取不到不阻斷
            pass

        class ClassifyDomain(dspy.Signature):
            """依決策樹（各域 core + 所需證據 + 跨域界線）把一則負向旅遊商品評論歸到唯一最貼切的歸因域 domain（機器值）。
            先排除商品頁描述問題(content)再往履約/現場/客服/客人；只能回決策樹列出的 domain 機器值，負向必歸一域。"""

            review: str = dspy.InputField(desc="旅客評論原文")
            decision_tree: str = dspy.InputField(desc="六域決策樹：domain｜核心｜所需證據 + 跨域界線")
            domain: str = dspy.OutputField(desc="唯一歸因域機器值")

        program = dspy.Predict(ClassifyDomain)

        def metric(gold, pred, trace=None, pred_name=None, pred_trace=None):
            got = (getattr(pred, "domain", "") or "").strip()
            want = gold.domain
            if got == want:
                return dspy.Prediction(score=1.0, feedback="域正確。")
            fb = (
                f"應歸域 {want}（{domain_core.get(want, '')}）。實際判「{got or '空'}」——"
                f"先辨識評論主體屬哪一域，再對照該域核心界線。"
            )
            return dspy.Prediction(score=0.0, feedback=fb)

        trainset = [
            dspy.Example(review=pc, decision_tree=dt_text, domain=domain).with_inputs("review", "decision_tree")
            for domain, _code, pc in _leaf_examples()
        ]
        return program, metric, trainset, _OUT_FILE["a"], ("review", "decision_tree")

    # stage in ("b", "legacy")：都判 L3 code；差別＝catalog 是單域(b) 還全域(legacy)
    single_domain = stage == "b"

    class AttributeReview(dspy.Signature):
        """依『問題分類 L3 目錄』把一則負向旅遊商品評論歸到最貼切的一條 L3，回其 code。
        只能從目錄內選 code；嚴格依細項邊界，無法明確歸類時回空字串（寧缺勿濫）。"""

        review: str = dspy.InputField(desc="旅客評論原文")
        catalog: str = dspy.InputField(desc="L3 目錄：code | 域›面向›細項 | 意義")
        l3_code: str = dspy.OutputField(desc="最貼切的一條 L3 code，或空字串")

    program = dspy.Predict(AttributeReview)

    def metric(gold, pred, trace=None, pred_name=None, pred_trace=None):
        got = (getattr(pred, "l3_code", "") or "").strip()
        want = gold.l3_code
        gnode = ai_judge.l3_by_code(want) or {}
        if got == want:
            return dspy.Prediction(score=1.0, feedback="正確歸類。")
        got_node = ai_judge.l3_by_code(got) if got in valid else {}
        # 單域(b)：同 L2 給部分分；全域(legacy)：同域給部分分
        if single_domain:
            same = bool(got_node) and got_node.get("l2_code") == gnode.get("l2_code")
            diag = "同 L2 但細項錯——注意兄弟細項邊界差異" if same else "L2 面向就選錯——先定面向再選細項"
        else:
            same = bool(got_node) and got_node.get("l1_domain") == gnode.get("l1_domain")
            diag = "域對但細項錯——注意兄弟細項的邊界差異" if same else "連歸因域都錯——先辨識評論主體屬哪一域"
        score = 0.3 if same else 0.0
        canon = (gnode.get("canon") or "")[:220]
        fb = (
            f"應歸 {want}（{gnode.get('l1_label','')}›{gnode.get('l2_label','')}›{gnode.get('l3_label','')}）。"
            f"其判準：{canon} 實際判「{got or '空'}」，{diag}。"
        )
        return dspy.Prediction(score=score, feedback=fb)

    # legacy：全域 catalog 固定；b：每例注入「該葉所屬單域」catalog
    full_catalog = _catalog_for_domains([]) if not single_domain else None
    trainset = []
    _domain_catalog_cache: dict[str, str] = {}
    for domain, code, pc in _leaf_examples():
        if single_domain:
            cat = _domain_catalog_cache.get(domain) or _catalog_for_domains([domain])
            _domain_catalog_cache[domain] = cat
        else:
            cat = full_catalog
        trainset.append(
            dspy.Example(review=pc, catalog=cat, l3_code=code).with_inputs("review", "catalog")
        )
    return program, metric, trainset, _OUT_FILE["b" if single_domain else "legacy"], ("review", "catalog")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=["legacy", "a", "b"], default="legacy",
                    help="優化階段：a=域分類 / b=單域 L2-L3 / legacy=單次全目錄（預設）")
    ap.add_argument("--smoke", action="store_true", help="只驗證接線（不真跑 GEPA）")
    ap.add_argument("--budget", choices=["light", "medium", "heavy"], help="真優化預算（auto）")
    ap.add_argument("--max-calls", type=int, default=None, help="硬上限 metric 呼叫數（取代 auto，早停快出）")
    ap.add_argument("--reflection-model", default=None, help="反射 LM 模型（建議較強者，如 gpt-5.4）")
    ap.add_argument("--model", default=None, help="student 分類模型（預設 user 設定；快跑指定 gpt-4.1-mini）")
    ap.add_argument("--threads", type=int, default=16, help="並行 API 呼叫數（I/O bound，受 provider RPM 限制）")
    ap.add_argument("--reasoning", default="minimal", help="student 的 reasoning_effort（minimal 最快）")
    args = ap.parse_args()

    try:
        import dspy
    except ImportError:
        sys.exit("dspy 未裝：backend/.venv/bin/pip install dspy")

    from app.core import ai_judge

    eff = _effective_llm()
    program, metric, trainset, out_file, input_names = _build(args.stage, dspy, ai_judge)

    valset = trainset[::5]  # 每 5 筆抽 1 (~20%) 進 valset，防 overfit（確定性分割）
    trainpart = [e for i, e in enumerate(trainset) if i % 5 != 0]
    print(f"stage={args.stage}｜trainset：{len(trainpart)} train / {len(valset)} val（C-2~C-6 positive_cases）", flush=True)

    dspy.configure(lm=_dspy_lm(dspy, eff, model=args.model, reasoning=args.reasoning))

    if args.smoke or (not args.budget and not args.max_calls):
        sample = trainset[:3]
        ok = 0
        for ex in sample:
            pred = program(**{k: getattr(ex, k) for k in input_names})
            r = metric(ex, pred)
            gold = getattr(ex, "domain", None) or getattr(ex, "l3_code", "?")
            got = getattr(pred, "domain", None) or getattr(pred, "l3_code", "?")
            ok += r.score == 1.0
            print(f"  [{gold}] 判→ {got} score={r.score}")
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
    print(f"GEPA 優化中 stage={args.stage}（{_b}, threads={args.threads}, model={args.model or 'default'}）", flush=True)
    optimized = optimizer.compile(program, trainset=trainpart, valset=valset)

    instr = optimized.signature.instructions if hasattr(optimized, "signature") else str(optimized)
    os.makedirs(os.path.dirname(out_file), exist_ok=True)
    open(out_file, "w", encoding="utf-8").write(instr)
    _target = {"a": "_stage_a_system", "b": "_attr_system（Stage B）", "legacy": "_ATTR_SYS"}[args.stage]
    print(f"\n=== 優化後指令 → {out_file} ===\n{instr}\n")
    print(f"回填：人審後把此指令覆蓋 backend/app/judge/prejudge.py 的 {_target}；"
          f"再開 cascade.enabled=true 跑 promptfoo 驗命中率不退步。")


if __name__ == "__main__":
    main()

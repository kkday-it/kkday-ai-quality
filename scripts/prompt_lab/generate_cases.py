"""Mock 樣本生成 CLI（PRD §7 / §15）——按 plan 逐格生成 CandidateCase。

Generator 只收「單格規格」（非 judge prompt），每格產 target_count 條；權威 expected_* 由
plan cell 決定（plan＝標籤 SSOT），LLM 只產文本 + 逐字證據 + 理由。去重＝NFKC+合併空白 exact hash。

用法見 PRD §15；支援 --dry-run（零 API）--limit --plan-id --workers --resume --model --all --confirm-cost。
"""

from __future__ import annotations

import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402
from openai_gateway import Gateway  # noqa: E402
from prompt_parser import parse_gen_prompt_file  # noqa: E402
from schemas import (  # noqa: E402
    GENERATOR_OUTPUT_SCHEMA,
    CandidateCase,
    GeneratorOutput,
    Plan,
    PlanCell,
    normalize_for_dedup,
    verbatim_grounded,
)

_GEN_PROMPT = (
    Path(__file__).resolve().parents[2]
    / "evals/prompt_lab/prompts/generators/c1_generator.md"
)


def build_spec(cell: PlanCell) -> str:
    """把 plan cell 轉為給 LLM 的可讀規格字串（不洩漏評測用語）。"""
    lines = [
        f"格 ID：{cell.cell_id}",
        f"層級：Layer {cell.layer}｜表達變體：{cell.expression_variant}｜難度：{cell.difficulty}",
        f"整體傾向：{cell.input_polarity}｜語言優先：zh-tw｜需產數量：{cell.target_count} 條",
    ]
    if cell.case_family == "contrast_pair":
        lines += [
            "類型：對照組——請產出恰好 1 對共 2 條：",
            f"  A 側（pair_side=A）：頁面資訊有問題（命中面向語義：{cell.coverage_note}）。",
            f"  B 側（pair_side=B）：頁面已寫清，真實責任在他域 {cell.boundary_with}。",
            f"  唯一可改變的責任事實（contrast_key）：{cell.contrast_theme}",
            "  兩側其餘商品情境盡量一致，只改這一個事實；A 側附逐字 evidence，B 側 evidence 回空。",
        ]
    elif cell.expected_domain == "true":
        lines += [
            f"標籤：正例（命中面向語義：{cell.coverage_note}）。",
            "必須：文本明確指稱『頁面/介紹/說明』的資訊寫錯/缺漏/模糊/誇大，且改頁面即可避免；附至少 1 條逐字 evidence_quote。",
        ]
    elif cell.expected_domain == "false":
        lines += [
            f"標籤：負例，真實責任明確在他域 {cell.boundary_with}（情境語義：{cell.coverage_note}）。",
            "必須：文本明確給出該他域責任事實，且不得暗含任何獨立成立的頁面資訊問題；evidence_quotes 回空。",
        ]
    else:  # uncertain
        lines += [
            f"標籤：不確定/證據不足（情境語義：{cell.coverage_note}）。",
            "必須：文本讓人無法只憑文字判斷是頁面寫錯、現場偏離還是旅客沒看；evidence_quotes 回空。",
        ]
    if cell.adversarial_techniques:
        lines += [
            f"對抗技術（{cell.target_count} 條各用一種，輪替覆蓋）：{', '.join(cell.adversarial_techniques)}",
            "對抗樣本仍須保有一個真實成立的頁面資訊問題；prompt_injection 僅作待判文本，不得寫成給系統的指令生效內容。",
        ]
    return "\n".join(lines)


def _mk_case(
    cell: PlanCell,
    co,
    *,
    case_id: str,
    expected_domain: str,
    plan_id: str,
    gen_model: str,
    req_id: str | None,
    contrast_pair_id: str | None,
) -> CandidateCase | None:
    """由 LLM 單條輸出 + cell 權威欄位組 CandidateCase；驗證失敗回 None。"""
    is_true = expected_domain == "true"
    l2 = list(cell.target_l2_codes) if is_true else []
    # evidence：只保留逐字落地的；非 true 一律清空
    quotes = (
        [q for q in (co.evidence_quotes or []) if verbatim_grounded(q, co.text)]
        if is_true
        else []
    )
    try:
        return CandidateCase(
            case_id=case_id,
            domain_under_test=cell.domain_under_test,
            layer=cell.layer,
            text=co.text,
            input_polarity=cell.input_polarity,
            expected_domain=expected_domain,  # type: ignore[arg-type]
            expected_l2_codes=l2,
            forbidden_l2_codes=[],
            expected_evidence_quotes=quotes,
            case_family=cell.case_family,
            expression_variant=cell.expression_variant,
            difficulty=cell.difficulty,
            language=co.language or "zh-tw",
            boundary_with=cell.boundary_with,
            contrast_pair_id=contrast_pair_id,
            contrast_key=cell.contrast_theme if contrast_pair_id else None,
            label_reason=co.label_reason,
            generator_model=gen_model,
            generator_request_id=req_id or "",
            generation_plan_id=cell.cell_id,
            origin="ai_generated",
            status="candidate",
        )
    except Exception as e:  # noqa: BLE001  驗證失敗（如 true 側無證據仍可，但 L2 不合法等）→ 記錄跳過
        print(
            f"  ⚠️ {case_id} 建構失敗：{str(e).splitlines()[-1][:80]}", file=sys.stderr
        )
        return None


def process_cell(
    gw: Gateway, gen_prompt, cell: PlanCell, model: str
) -> tuple[list[CandidateCase], str | None]:
    """處理單格：呼叫 gateway → 解析 GeneratorOutput → 組 CandidateCase 清單。回 (cases, error)。"""
    spec = build_spec(cell)
    res = gw.structured(
        system=gen_prompt.system,
        user=gen_prompt.render_user(spec),
        json_schema=GENERATOR_OUTPUT_SCHEMA,
        schema_name="generator_output",
        model=model,
        meta={
            "cell_id": cell.cell_id,
            "plan_id": cell.cell_id,
            "prompt_sha256": gen_prompt.sha256,
        },
    )
    if not res.ok:
        return [], res.error
    try:
        out = GeneratorOutput(**res.parsed)
    except Exception as e:  # noqa: BLE001
        return [], f"schema_invalid:{str(e).splitlines()[-1][:60]}"
    slots = common.slot_case_ids(cell.cell_id, cell.case_family, cell.target_count)
    cases: list[CandidateCase] = []
    if cell.case_family == "contrast_pair":
        pair_id = f"pair-{cell.cell_id}"
        by_side = {co.pair_side: co for co in out.cases if co.pair_side in ("A", "B")}
        # 若 LLM 未標 side，退回依序 A,B
        a = by_side.get("A") or (out.cases[0] if out.cases else None)
        b = by_side.get("B") or (out.cases[1] if len(out.cases) > 1 else None)
        if a:
            c = _mk_case(
                cell,
                a,
                case_id=f"{cell.cell_id}-a",
                expected_domain="true",
                plan_id=cell.cell_id,
                gen_model=res.model,
                req_id=res.request_id,
                contrast_pair_id=pair_id,
            )
            if c:
                cases.append(c)
        if b:
            c = _mk_case(
                cell,
                b,
                case_id=f"{cell.cell_id}-b",
                expected_domain="false",
                plan_id=cell.cell_id,
                gen_model=res.model,
                req_id=res.request_id,
                contrast_pair_id=pair_id,
            )
            if c:
                cases.append(c)
    else:
        for slot, co in zip(slots, out.cases[: cell.target_count]):
            c = _mk_case(
                cell,
                co,
                case_id=slot,
                expected_domain=cell.expected_domain,
                plan_id=cell.cell_id,
                gen_model=res.model,
                req_id=res.request_id,
                contrast_pair_id=None,
            )
            if c:
                cases.append(c)
    return cases, None


def main(argv: list[str] | None = None, *, gateway: Gateway | None = None) -> int:
    """CLI 入口。gateway 可注入（fake client 測試用）；None 則依 env 建真 gateway。"""
    common.load_env()  # 先載入 evals/prompt_lab/.env（真實 env 優先），再讓下方 default 讀 env
    ap = argparse.ArgumentParser(description="C-1 Mock 樣本生成")
    ap.add_argument("--plan", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default=os.environ.get("PROMPT_LAB_GENERATOR_MODEL", ""))
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument(
        "--limit", type=int, default=5, help="預設成本上限（格數）；超過需 --all"
    )
    ap.add_argument("--plan-id", default="", help="只跑指定 cell_id（子串比對）")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument(
        "--dry-run", action="store_true", help="零 API：只印待處理格數與規格範例"
    )
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--confirm-cost", action="store_true")
    args = ap.parse_args(argv)

    plan = Plan(**_load_json(args.plan))
    gen_prompt = parse_gen_prompt_file(_GEN_PROMPT)
    cells = [c for c in plan.cells if (not args.plan_id or args.plan_id in c.cell_id)]

    existing = (
        {c["case_id"]: c for c in common.read_jsonl(args.out)} if args.resume else {}
    )
    todo: list[PlanCell] = []
    for cell in cells:
        slots = common.slot_case_ids(cell.cell_id, cell.case_family, cell.target_count)
        if args.resume and all(s in existing for s in slots):
            continue
        todo.append(cell)

    print(
        f"計畫 {plan.plan_id}：{len(cells)} 格；待處理 {len(todo)} 格（resume 已跳過 {len(cells) - len(todo)}）"
    )
    if args.dry_run:
        print(f"🔎 dry-run：將發出 {len(todo)} 次生成呼叫（零 API）。範例規格：")
        if todo:
            print("---\n" + build_spec(todo[0]) + "\n---")
        return 0

    if not args.model:
        print("⛔ 需 --model 或環境變數 PROMPT_LAB_GENERATOR_MODEL", file=sys.stderr)
        return 2
    allowed = common.confirm_cost_or_exit(
        len(todo), all_flag=args.all, confirm_cost=args.confirm_cost, limit=args.limit
    )
    todo = todo[:allowed]

    gw = gateway or Gateway()
    if not gw.has_key:
        print("⛔ 無 OPENAI_API_KEY（且未注入 client），無法真打", file=sys.stderr)
        return 2

    merged = dict(existing)
    seen_text = {normalize_for_dedup(c["text"]) for c in existing.values()}
    dups = 0
    fails: list[str] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        futs = {
            ex.submit(process_cell, gw, gen_prompt, cell, args.model): cell
            for cell in todo
        }
        for fut in futs:
            cell = futs[fut]
            cases, err = fut.result()
            if err:
                fails.append(f"{cell.cell_id}:{err}")
            for c in cases:
                nt = normalize_for_dedup(c.text)
                if nt in seen_text:
                    dups += 1
                    continue
                seen_text.add(nt)
                merged[c.case_id] = c.model_dump()

    common.write_jsonl(args.out, [merged[k] for k in sorted(merged)])
    print(
        f"✅ 產出 {len(merged)} 條（新增去重後）；重複丟棄 {dups}；失敗格 {len(fails)}"
    )
    if fails:
        print("失敗格：" + "; ".join(fails[:10]), file=sys.stderr)
    return 0


def _load_json(path: str) -> dict:
    import json

    return json.loads(Path(path).read_text(encoding="utf-8"))


if __name__ == "__main__":
    raise SystemExit(main())

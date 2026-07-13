"""C-1 Judge Runner（PRD §10 / §11 / §13）——跑被測 prompt、記錄每次原始結果、算指標、產報告。

契約（§4.1）：attributions.length>0 → predicted_domain_hit=true。每 case 跑 repeats 次（預設 3），
真實呼叫、禁用快取、不做多數投票（§10.4）。resume 以 case+repeat+prompt_sha+model 跳過**成功**項；
Schema 失敗/拒答/空輸出/incomplete 分別記錄，NEVER 當作棄權。

輸出 out 目錄：raw_results.jsonl、run_manifest.json，並呼叫 report 產 metrics/summary/CSV（§13）。
"""

from __future__ import annotations

import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402
import report as report_mod  # noqa: E402
from openai_gateway import Gateway  # noqa: E402
from prompt_parser import parse_prompt_file  # noqa: E402
from schemas import JudgeRunResult, verbatim_grounded  # noqa: E402

_DEFAULT_PROMPT = (
    Path(__file__).resolve().parents[2]
    / "evals/prompt_lab/prompts/judges/01_C-1_content.md"
)


def _run_key(case_id: str, repeat: int, prompt_sha: str, model: str) -> str:
    """resume 唯一鍵：case+repeat+prompt hash 短碼+model。"""
    return f"{case_id}|{repeat}|{prompt_sha[:12]}|{model}"


def judge_once(
    gw: Gateway, parsed, case: dict, repeat: int, model: str, run_id: str
) -> JudgeRunResult:
    """對單一 case 跑一次 judge，回 JudgeRunResult（含各類失敗分類）。"""
    text = case["text"]
    user = parsed.render_user(case["input_polarity"], text)
    res = gw.structured(
        system=parsed.system,
        user=user,
        json_schema=parsed.schema,
        schema_name="c1_attribution",
        model=model,
        meta={
            "case_id": case["case_id"],
            "repeat": repeat,
            "prompt_sha256": parsed.sha256,
        },
    )
    base = dict(
        run_id=run_id,
        case_id=case["case_id"],
        repeat_index=repeat,
        prompt_version=parsed.version,
        prompt_sha256=parsed.sha256,
        model=res.model,
        request_id=res.request_id,
        raw_output=res.raw_output,
        latency_ms=res.latency_ms,
        input_tokens=res.input_tokens,
        output_tokens=res.output_tokens,
        attempts=res.attempts,
    )
    if not res.ok:
        # 失敗分類原樣記入 error（schema_invalid/refusal/incomplete/empty/api:…）；schema_valid=False
        err = "schema_invalid" if res.error == "parse_error" else res.error
        return JudgeRunResult(
            **base, schema_valid=False, predicted_domain_hit=None, error=err
        )
    attrs = res.parsed.get("attributions", []) if isinstance(res.parsed, dict) else []
    if not isinstance(attrs, list):
        return JudgeRunResult(
            **base,
            schema_valid=False,
            predicted_domain_hit=None,
            error="schema_invalid",
        )
    l2 = [a.get("l2_code") for a in attrs if isinstance(a, dict)]
    quotes = [a.get("evidence_quote", "") for a in attrs if isinstance(a, dict)]
    confs = [
        float(a.get("confidence"))
        for a in attrs
        if isinstance(a, dict) and a.get("confidence") is not None
    ]
    grounded = all(verbatim_grounded(q, text) for q in quotes) if quotes else None
    return JudgeRunResult(
        **base,
        schema_valid=True,
        predicted_domain_hit=len(attrs) > 0,
        predicted_l2_codes=[c for c in l2 if c],
        predicted_evidence_quotes=quotes,
        predicted_confidences=confs,
        evidence_grounded=grounded,
        error=None,
    )


def _filter_cases(cases: list[dict], args) -> list[dict]:
    """依 --layer/--case-id/--slice field=value/--limit 過濾 case。"""
    out = cases
    if args.layer:
        out = [c for c in out if c.get("layer") == args.layer]
    if args.case_id:
        out = [c for c in out if c["case_id"] == args.case_id]
    for sl in args.slice or []:
        if "=" in sl:
            k, v = sl.split("=", 1)
            out = [c for c in out if str(c.get(k, "")) == v]
    if args.limit and args.limit > 0:
        out = out[: args.limit]
    return out


def main(argv: list[str] | None = None, *, gateway: Gateway | None = None) -> int:
    """CLI 入口。gateway 可注入（fake client 測試）。"""
    ap = argparse.ArgumentParser(description="C-1 Judge Runner")
    ap.add_argument("--prompt", default=str(_DEFAULT_PROMPT))
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--model", default=os.environ.get("PROMPT_LAB_JUDGE_MODEL", ""))
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--out", required=True, help="輸出目錄")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--layer", type=int, default=0)
    ap.add_argument("--case-id", default="")
    ap.add_argument("--slice", action="append", help="field=value 過濾（可多次）")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument(
        "--no-cache",
        action="store_true",
        help="（本 lab gateway 恆不快取；旗標僅為契約對齊）",
    )
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--sync", action="store_true", default=True)
    ap.add_argument("--batch", action="store_true", help="Phase 5（可選），MVP 未啟用")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--confirm-cost", action="store_true")
    args = ap.parse_args(argv)

    if args.batch:
        print(
            "⛔ --batch 為 Phase 5（可選），MVP 尚未啟用；請用同步模式（預設）。",
            file=sys.stderr,
        )
        return 2

    parsed = parse_prompt_file(args.prompt)  # 缺段/schema 非法/占位符缺失 → 立即失敗
    cases = _filter_cases(common.read_jsonl(args.dataset), args)
    out_dir = Path(args.out)
    raw_path = out_dir / "raw_results.jsonl"

    # 計畫全部 run keys；resume 跳過已成功者
    existing = {}
    if args.resume:
        for r in common.read_jsonl(raw_path):
            existing[
                _run_key(
                    r["case_id"], r["repeat_index"], r["prompt_sha256"], r["model"]
                )
            ] = r
    jobs = []  # (case, repeat)
    for c in cases:
        for rep in range(args.repeats):
            k = _run_key(c["case_id"], rep, parsed.sha256, args.model or "?")
            prev = existing.get(k)
            if args.resume and prev is not None and prev.get("error") is None:
                continue  # 只跳過成功項
            jobs.append((c, rep))

    print(
        f"prompt {parsed.version}({parsed.sha256[:12]})｜dataset {len(cases)} case × repeats {args.repeats}｜待跑 {len(jobs)} runs"
    )
    if args.dry_run:
        print(f"🔎 dry-run：將發出 {len(jobs)} 次 judge 呼叫（零 API）。")
        return 0
    if not args.model:
        print("⛔ 需 --model 或 PROMPT_LAB_JUDGE_MODEL", file=sys.stderr)
        return 2
    allowed = common.confirm_cost_or_exit(
        len(jobs),
        all_flag=args.all,
        confirm_cost=args.confirm_cost,
        limit=max(5, args.repeats),
    )
    jobs = jobs[:allowed]

    gw = gateway or Gateway()
    if not gw.has_key:
        print("⛔ 無 OPENAI_API_KEY（且未注入 client）", file=sys.stderr)
        return 2

    run_id = f"{parsed.version}-{parsed.sha256[:8]}"
    results = dict(existing)  # key -> result dict
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        futs = {
            ex.submit(judge_once, gw, parsed, c, rep, args.model, run_id): (c, rep)
            for c, rep in jobs
        }
        for fut in futs:
            r = fut.result()
            results[_run_key(r.case_id, r.repeat_index, r.prompt_sha256, r.model)] = (
                r.model_dump()
            )

    all_results = [results[k] for k in sorted(results)]
    common.write_jsonl(raw_path, all_results)

    n_err = sum(1 for r in all_results if r.get("error"))
    manifest = {
        "run_id": run_id,
        "prompt_path": str(args.prompt),
        "prompt_version": parsed.version,
        "prompt_sha256": parsed.sha256,
        "dataset": args.dataset,
        "model": args.model,
        "repeats": args.repeats,
        "n_cases": len(cases),
        "n_runs": len(all_results),
        "n_errors": n_err,
        "total_input_tokens": sum(r.get("input_tokens") or 0 for r in all_results),
        "total_output_tokens": sum(r.get("output_tokens") or 0 for r in all_results),
        "total_latency_ms": sum(r.get("latency_ms") or 0 for r in all_results),
        "filters": {
            "layer": args.layer,
            "case_id": args.case_id,
            "slice": args.slice,
            "limit": args.limit,
        },
    }
    (out_dir / "run_manifest.json").write_text(
        __import__("json").dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    # 產 metrics / summary / CSV（§13）
    report_mod.write_reports(out_dir, cases, all_results, manifest)
    print(f"✅ {len(all_results)} runs（{n_err} 失敗）→ {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

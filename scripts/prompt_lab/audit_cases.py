"""Mock 樣本審核 CLI（PRD §6.2 / §8）——Auditor 獨立審題 + 產人工複核佇列。

Auditor 不使用被測 judge prompt；對每條 candidate 獨立判斷標籤是否成立/自包含/唯一/證據落地。
自動判 review_required 條件（§6.2）：標籤或 L2 不一致、ambiguous、不自包含、證據不落地、
負例含獨立 C-1、近重複、Schema 非法。

review_queue.csv（§8）：所有 review_required + 所有 uncertain + 所有 contrast pair
+ 其餘自動通過的分層隨機 20%（固定 seed 可複現）。支援 accept|edit|reject。
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import random
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402
from openai_gateway import Gateway  # noqa: E402
from prompt_parser import parse_gen_prompt_file  # noqa: E402
from schemas import AUDITOR_OUTPUT_SCHEMA, AuditorOutput, CandidateCase  # noqa: E402

_AUDIT_PROMPT = (
    Path(__file__).resolve().parents[2]
    / "evals/prompt_lab/prompts/generators/c1_auditor.md"
)
_REVIEW_SAMPLE_RATE = 0.20  # §8：自動通過樣本的分層抽檢比例
_REVIEW_SEED = 42  # 固定 seed → 抽檢可複現


def build_audit_spec(cand: CandidateCase) -> str:
    """把候選樣本 + 其被賦予標籤組成給 Auditor 的審核規格。"""
    lines = [
        f"候選 case_id：{cand.case_id}",
        "評論文本：",
        f"「{cand.text}」",
        f"被賦予標籤：expected_domain={cand.expected_domain}｜expected_l2_codes={cand.expected_l2_codes}",
        f"case_family={cand.case_family}｜boundary_with={cand.boundary_with}｜input_polarity={cand.input_polarity}",
        f"附帶 evidence_quotes：{cand.expected_evidence_quotes}",
    ]
    if cand.contrast_pair_id:
        lines.append(
            f"對照組：contrast_pair_id={cand.contrast_pair_id}，contrast_key=「{cand.contrast_key}」；本條為該對之一側。"
        )
    lines.append("請獨立判斷並填寫全部審核欄位。")
    return "\n".join(lines)


def decide_status(cand: CandidateCase, audit: AuditorOutput) -> tuple[str, list[str]]:
    """依 §6.2 規則計算審核 status 與觸發原因清單。"""
    reasons: list[str] = []
    if audit.suggested_domain != cand.expected_domain:
        reasons.append(
            f"domain不一致(gen={cand.expected_domain},audit={audit.suggested_domain})"
        )
    if cand.expected_domain == "true" and sorted(audit.suggested_l2_codes) != sorted(
        cand.expected_l2_codes
    ):
        reasons.append(
            f"L2不一致(gen={cand.expected_l2_codes},audit={audit.suggested_l2_codes})"
        )
    if audit.ambiguous:
        reasons.append("ambiguous")
    if not audit.self_contained:
        reasons.append("not_self_contained")
    if not audit.label_supported:
        reasons.append("label_unsupported")
    if not audit.evidence_quotes_valid:
        reasons.append("evidence_invalid")
    if cand.expected_domain == "false" and audit.contains_independent_c1_issue:
        reasons.append("negative_hides_c1")
    if audit.near_duplicate:
        reasons.append("near_duplicate")
    return ("review_required" if reasons else "accepted"), reasons


def audit_one(
    gw: Gateway, audit_prompt, cand: CandidateCase, model: str
) -> tuple[dict | None, str | None]:
    """審核單條；回 (audit_result_dict, error)。"""
    res = gw.structured(
        system=audit_prompt.system,
        user=audit_prompt.render_user(build_audit_spec(cand)),
        json_schema=AUDITOR_OUTPUT_SCHEMA,
        schema_name="auditor_output",
        model=model,
        meta={"case_id": cand.case_id, "prompt_sha256": audit_prompt.sha256},
    )
    if not res.ok:
        return None, res.error
    try:
        out = AuditorOutput(**res.parsed)
    except Exception as e:  # noqa: BLE001
        return None, f"schema_invalid:{str(e).splitlines()[-1][:60]}"
    status, _reasons = decide_status(cand, out)
    from schemas import AuditResult

    ar = AuditResult(
        case_id=cand.case_id,
        label_supported=out.label_supported,
        ambiguous=out.ambiguous,
        self_contained=out.self_contained,
        contains_independent_c1_issue=out.contains_independent_c1_issue,
        suggested_domain=out.suggested_domain,
        suggested_l2_codes=out.suggested_l2_codes,
        evidence_quotes_valid=out.evidence_quotes_valid,
        near_duplicate=out.near_duplicate,
        audit_reason=out.audit_reason,
        auditor_model=res.model,
        auditor_request_id=res.request_id or "",
        status=status,  # type: ignore[arg-type]
    )
    return ar.model_dump(), None


def build_review_queue(
    cands: dict[str, CandidateCase], audits: dict[str, dict]
) -> list[dict]:
    """依 §8 選出需人工複核者：review_required + uncertain + contrast pair + 其餘 20% 分層抽檢。"""
    must: dict[str, str] = {}  # case_id -> review_reason
    auto_pass_pool: list[str] = []
    for cid, cand in cands.items():
        au = audits.get(cid)
        if au is None:
            must[cid] = "no_audit"
            continue
        if au["status"] == "review_required":
            _, reasons = decide_status(
                cand, AuditorOutput(**{k: au[k] for k in AuditorOutput.model_fields})
            )
            must[cid] = "review_required:" + ",".join(reasons)
        elif cand.expected_domain == "uncertain":
            must[cid] = "uncertain"
        elif cand.contrast_pair_id:
            must[cid] = "contrast_pair"
        else:
            auto_pass_pool.append(cid)
    # 分層隨機 20%（層＝layer|expected_domain|case_family|boundary_with）
    strata: dict[tuple, list[str]] = {}
    for cid in auto_pass_pool:
        c = cands[cid]
        key = (c.layer, c.expected_domain, c.case_family, c.boundary_with)
        strata.setdefault(key, []).append(cid)
    rng = random.Random(_REVIEW_SEED)
    for key, ids in strata.items():
        ids_sorted = sorted(ids)
        rng.shuffle(ids_sorted)
        k = math.ceil(len(ids_sorted) * _REVIEW_SAMPLE_RATE)
        for cid in ids_sorted[:k]:
            must[cid] = "stratified_sample_20pct"
    # 組 rows
    rows = []
    for cid in sorted(must):
        c = cands[cid]
        au = audits.get(cid, {})
        rows.append(
            {
                "case_id": cid,
                "review_reason": must[cid],
                "layer": c.layer,
                "case_family": c.case_family,
                "expected_domain": c.expected_domain,
                "expected_l2_codes": "|".join(c.expected_l2_codes),
                "boundary_with": c.boundary_with or "",
                "contrast_pair_id": c.contrast_pair_id or "",
                "contrast_key": c.contrast_key or "",
                "text": c.text,
                "auditor_suggested_domain": au.get("suggested_domain", ""),
                "auditor_suggested_l2": "|".join(au.get("suggested_l2_codes", [])),
                "audit_status": au.get("status", ""),
                "audit_reason": au.get("audit_reason", ""),
                # 人工填寫欄位
                "decision": "",  # accept | edit | reject
                "edited_text": "",
                "edited_domain": "",
                "edited_l2_codes": "",
                "notes": "",
            }
        )
    return rows


_REVIEW_COLUMNS = [
    "case_id",
    "review_reason",
    "layer",
    "case_family",
    "expected_domain",
    "expected_l2_codes",
    "boundary_with",
    "contrast_pair_id",
    "contrast_key",
    "text",
    "auditor_suggested_domain",
    "auditor_suggested_l2",
    "audit_status",
    "audit_reason",
    "decision",
    "edited_text",
    "edited_domain",
    "edited_l2_codes",
    "notes",
]


def write_review_queue(path: str, rows: list[dict]) -> None:
    """寫 review_queue.csv（UTF-8-SIG 讓 Excel 正確辨識中文）。"""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=_REVIEW_COLUMNS)
        w.writeheader()
        w.writerows(rows)


def main(argv: list[str] | None = None, *, gateway: Gateway | None = None) -> int:
    """CLI 入口。gateway 可注入（fake client 測試）。"""
    common.load_env()
    ap = argparse.ArgumentParser(description="C-1 Mock 樣本審核")
    ap.add_argument("--input", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--review-queue", required=True)
    ap.add_argument("--model", default=os.environ.get("PROMPT_LAB_AUDITOR_MODEL", ""))
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--confirm-cost", action="store_true")
    args = ap.parse_args(argv)

    cands = {r["case_id"]: CandidateCase(**r) for r in common.read_jsonl(args.input)}
    audit_prompt = parse_gen_prompt_file(_AUDIT_PROMPT)
    existing = (
        {r["case_id"]: r for r in common.read_jsonl(args.out)} if args.resume else {}
    )
    todo = [c for cid, c in cands.items() if cid not in existing]

    print(
        f"審核輸入 {len(cands)} 條；待審 {len(todo)}（resume 已跳 {len(cands) - len(todo)}）"
    )
    if args.dry_run:
        print(f"🔎 dry-run：將發出 {len(todo)} 次審核呼叫（零 API）。")
        return 0
    if not args.model:
        print("⛔ 需 --model 或 PROMPT_LAB_AUDITOR_MODEL", file=sys.stderr)
        return 2
    allowed = common.confirm_cost_or_exit(
        len(todo), all_flag=args.all, confirm_cost=args.confirm_cost, limit=args.limit
    )
    todo = todo[:allowed]

    gw = gateway or Gateway()
    if not gw.has_key:
        print("⛔ 無 OPENAI_API_KEY（且未注入 client）", file=sys.stderr)
        return 2

    audits = dict(existing)
    fails: list[str] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        futs = {ex.submit(audit_one, gw, audit_prompt, c, args.model): c for c in todo}
        for fut in futs:
            c = futs[fut]
            ar, err = fut.result()
            if err:
                fails.append(f"{c.case_id}:{err}")
            elif ar:
                audits[c.case_id] = ar

    common.write_jsonl(args.out, [audits[k] for k in sorted(audits)])
    # review queue 涵蓋所有有 candidate 者（無論本輪是否重審）
    rq = build_review_queue(cands, audits)
    write_review_queue(args.review_queue, rq)
    n_rev = sum(1 for a in audits.values() if a["status"] == "review_required")
    print(
        f"✅ 審核 {len(audits)} 條；review_required {n_rev}；review_queue {len(rq)} 條 → {args.review_queue}；失敗 {len(fails)}"
    )
    if fails:
        print("失敗：" + "; ".join(fails[:10]), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

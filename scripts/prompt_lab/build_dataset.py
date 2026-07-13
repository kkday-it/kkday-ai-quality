"""資料集冻結 CLI（PRD §6.3 / §9）——套用人工複核 → 分層切分 → 防泄漏 → manifest+SHA-256。

純邏輯（零 API）。70% Dev / 30% Holdout；同一 contrast_pair 必進同一 split；固定 seed 可複現；
檢查 case_id / exact text / contrast_pair 皆無跨集泄漏（發現即 fail-loud）。任何修改產生新 version+hash。

納入冻結的判準（對齊 §8 必審規則）：
- 人工 decision=accept/edit → 納入（edit 套用修改、origin=human_edited）；reject → 剔除；
- 無人工決定：僅當 auditor status=accepted 且 非 uncertain 且 非 contrast_pair 時納入（其餘必經人工）。
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path
from random import Random

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402
from schemas import CandidateCase, FrozenCase, normalize_for_dedup, verbatim_grounded  # noqa: E402

DEV_RATIO = 0.70  # PRD §9：70% Dev / 30% Holdout


def load_candidates(path: str) -> dict[str, CandidateCase]:
    """讀候選 JSONL；重複 case_id 立即拒絕（PRD §19：重複 id 拒絕，不得靜默覆蓋）。"""
    seen: dict[str, CandidateCase] = {}
    for r in common.read_jsonl(path):
        cid = r["case_id"]
        if cid in seen:
            raise ValueError(f"重複 case_id：{cid}（冻結前必須唯一）")
        seen[cid] = CandidateCase(**r)
    return seen


def _load_human(path: str | None) -> dict[str, dict]:
    """讀人工複核 CSV（有 decision 者才算數）；回 case_id -> row。"""
    if not path or not Path(path).exists():
        return {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        return {
            r["case_id"]: r
            for r in csv.DictReader(f)
            if (r.get("decision") or "").strip()
        }


def _apply_edit(cand: CandidateCase, row: dict) -> CandidateCase:
    """套用人工編輯（edited_text/domain/l2）；重算逐字證據；origin=human_edited。"""
    data = cand.model_dump()
    if (row.get("edited_text") or "").strip():
        data["text"] = row["edited_text"].strip()
    if (row.get("edited_domain") or "").strip():
        data["expected_domain"] = row["edited_domain"].strip()
    if (row.get("edited_l2_codes") or "").strip():
        data["expected_l2_codes"] = [
            x for x in row["edited_l2_codes"].split("|") if x.strip()
        ]
    if data["expected_domain"] != "true":
        data["expected_l2_codes"] = []
    # 重算證據：只保留仍逐字落地者
    data["expected_evidence_quotes"] = [
        q
        for q in data["expected_evidence_quotes"]
        if verbatim_grounded(q, data["text"])
    ]
    data["origin"] = "human_edited"
    return CandidateCase(**data)


def select_frozen(
    cands: dict[str, CandidateCase], audits: dict[str, dict], human: dict[str, dict]
) -> tuple[list[CandidateCase], dict]:
    """依納入判準選出待冻結 candidate（已套用人工編輯），回 (cases, stats)。"""
    kept: list[CandidateCase] = []
    stats = {
        "rejected": 0,
        "auto_passed": 0,
        "human_accepted": 0,
        "human_edited": 0,
        "excluded_unreviewed": 0,
    }
    for cid, cand in cands.items():
        row = human.get(cid)
        dec = (row.get("decision") if row else "").strip().lower()
        au = audits.get(cid)
        if dec == "reject":
            stats["rejected"] += 1
            continue
        if dec == "edit":
            kept.append(_apply_edit(cand, row))
            stats["human_edited"] += 1
        elif dec == "accept":
            kept.append(cand)
            stats["human_accepted"] += 1
        elif (
            au
            and au.get("status") == "accepted"
            and cand.expected_domain != "uncertain"
            and not cand.contrast_pair_id
        ):
            kept.append(cand)
            stats["auto_passed"] += 1
        else:
            stats["excluded_unreviewed"] += 1
    return kept, stats


def _stratum(c: CandidateCase) -> tuple:
    """分層鍵：contrast pair 用 (layer,pair,boundary)（pair 內兩側一致）；其餘 (layer,family,primary)。"""
    if c.contrast_pair_id:
        return (c.layer, "contrast_pair", c.boundary_with or "none")
    if c.expected_domain == "true" and c.expected_l2_codes:
        primary = c.expected_l2_codes[0]
    else:
        primary = c.boundary_with or c.expected_domain
    return (c.layer, c.case_family, primary)


def split_dev_holdout(
    cases: list[CandidateCase], seed: int
) -> tuple[list[CandidateCase], list[CandidateCase]]:
    """分層 70/30 切分，contrast pair 整組同 split，固定 seed 可複現。"""
    # 組 unit：pair 兩側綁一起
    by_pair: dict[str, list[CandidateCase]] = defaultdict(list)
    units: list[list[CandidateCase]] = []
    for c in cases:
        if c.contrast_pair_id:
            by_pair[c.contrast_pair_id].append(c)
        else:
            units.append([c])
    units.extend(by_pair.values())
    strata: dict[tuple, list[list[CandidateCase]]] = defaultdict(list)
    for u in units:
        strata[_stratum(u[0])].append(u)
    rng = Random(seed)
    dev: list[CandidateCase] = []
    holdout: list[CandidateCase] = []
    for key in sorted(strata, key=str):
        us = sorted(strata[key], key=lambda u: u[0].case_id)
        rng.shuffle(us)
        n_dev = round(len(us) * DEV_RATIO)
        for i, u in enumerate(us):
            (dev if i < n_dev else holdout).extend(u)
    return dev, holdout


def assert_no_leak(dev: list[CandidateCase], holdout: list[CandidateCase]) -> dict:
    """檢查 case_id / exact text / contrast_pair 皆無跨集泄漏；發現即 raise。"""
    checks = {}
    dev_ids, ho_ids = {c.case_id for c in dev}, {c.case_id for c in holdout}
    if dev_ids & ho_ids:
        raise ValueError(f"case_id 跨集泄漏：{sorted(dev_ids & ho_ids)[:5]}")
    checks["case_id"] = "pass"
    dev_txt, ho_txt = {c.text for c in dev}, {c.text for c in holdout}
    if dev_txt & ho_txt:
        raise ValueError("exact text 跨集泄漏")
    # 正規化後也檢查（防 NFKC/空白差異的近同）
    if {normalize_for_dedup(t) for t in dev_txt} & {
        normalize_for_dedup(t) for t in ho_txt
    }:
        raise ValueError("normalized text 跨集泄漏")
    checks["exact_text"] = "pass"
    dev_pairs = {c.contrast_pair_id for c in dev if c.contrast_pair_id}
    ho_pairs = {c.contrast_pair_id for c in holdout if c.contrast_pair_id}
    if dev_pairs & ho_pairs:
        raise ValueError(f"contrast_pair 跨集泄漏：{sorted(dev_pairs & ho_pairs)[:5]}")
    checks["contrast_pair"] = "pass"
    return checks


def to_frozen(
    c: CandidateCase,
    audits: dict[str, dict],
    human: dict[str, dict],
    version: str,
    split: str,
) -> FrozenCase:
    """CandidateCase → FrozenCase（去長推理，留評測欄位 + 精簡審核元資料 + 版本/切分）。"""
    au = audits.get(c.case_id, {})
    reviewed = c.case_id in human or c.origin == "human_edited"
    evidence_valid = all(
        verbatim_grounded(q, c.text) for q in c.expected_evidence_quotes
    )
    return FrozenCase(
        case_id=c.case_id,
        domain_under_test=c.domain_under_test,
        layer=c.layer,
        text=c.text,
        input_polarity=c.input_polarity,
        expected_domain=c.expected_domain,
        expected_l2_codes=c.expected_l2_codes,
        forbidden_l2_codes=c.forbidden_l2_codes,
        expected_evidence_quotes=c.expected_evidence_quotes,
        case_family=c.case_family,
        expression_variant=c.expression_variant,
        difficulty=c.difficulty,
        language=c.language,
        boundary_with=c.boundary_with,
        contrast_pair_id=c.contrast_pair_id,
        contrast_key=c.contrast_key,
        label_reason=c.label_reason,
        origin=c.origin,
        label_supported=bool(au.get("label_supported", True)),
        evidence_quotes_valid=evidence_valid,
        dataset_version=version,
        split=split,
        human_reviewed=reviewed,
    )


def coverage_matrix(cases: list[FrozenCase]) -> dict:
    """按 (layer, case_family, expected_domain) 統計覆蓋數，供 manifest 與品質報告。"""
    m: dict[str, int] = defaultdict(int)
    for c in cases:
        m[f"L{c.layer}|{c.case_family}|{c.expected_domain}"] += 1
    return dict(sorted(m.items()))


def main(argv: list[str] | None = None) -> int:
    """CLI 入口。"""
    ap = argparse.ArgumentParser(description="C-1 資料集冻結")
    ap.add_argument("--candidates", required=True)
    ap.add_argument("--audits", default="")
    ap.add_argument("--human-review", default="")
    ap.add_argument("--dataset-version", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--split-seed", type=int, default=42)
    args = ap.parse_args(argv)

    cands = load_candidates(args.candidates)
    audits = (
        {r["case_id"]: r for r in common.read_jsonl(args.audits)} if args.audits else {}
    )
    human = _load_human(args.human_review)

    kept, stats = select_frozen(cands, audits, human)
    if not kept:
        print(
            "⛔ 無任何樣本通過納入判準（需人工複核 accept/edit 或 auditor accepted）",
            file=sys.stderr,
        )
        return 2
    dev_c, ho_c = split_dev_holdout(kept, args.split_seed)
    checks = assert_no_leak(dev_c, ho_c)

    ver = args.dataset_version
    dev = [
        to_frozen(c, audits, human, ver, "dev")
        for c in sorted(dev_c, key=lambda c: c.case_id)
    ]
    ho = [
        to_frozen(c, audits, human, ver, "holdout")
        for c in sorted(ho_c, key=lambda c: c.case_id)
    ]

    out_dir = Path(args.out_dir)
    dev_path = out_dir / f"{ver}-dev.jsonl"
    ho_path = out_dir / f"{ver}-holdout.jsonl"
    common.write_jsonl(dev_path, dev)
    common.write_jsonl(ho_path, ho)

    gen_models = sorted({c.generator_model for c in kept if c.generator_model})
    manifest = {
        "dataset_version": ver,
        "split_seed": args.split_seed,
        "dev_ratio": DEV_RATIO,
        "counts": {"total": len(kept), "dev": len(dev), "holdout": len(ho)},
        "selection_stats": stats,
        "coverage_dev": coverage_matrix(dev),
        "coverage_holdout": coverage_matrix(ho),
        "leak_checks": checks,
        "files": {
            "dev": {
                "path": str(dev_path),
                "sha256": common.sha256_file(dev_path),
                "n": len(dev),
            },
            "holdout": {
                "path": str(ho_path),
                "sha256": common.sha256_file(ho_path),
                "n": len(ho),
            },
        },
        "provenance": {
            "generator_models": gen_models,
            "auditor_models": sorted(
                {
                    a.get("auditor_model", "")
                    for a in audits.values()
                    if a.get("auditor_model")
                }
            ),
            "human_reviewed": sum(1 for c in dev + ho if c.human_reviewed),
        },
        "prompt_manifest_ref": "../../prompts/prompts_manifest.json",
        "limitations": _limitations(kept, audits),
    }
    man_path = out_dir / f"{ver}-manifest.json"
    import json

    man_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"✅ 冻結 {ver}：dev {len(dev)} / holdout {len(ho)}（total {len(kept)}）")
    print(f"   防泄漏：{checks} ｜ selection={stats}")
    print(f"   → {dev_path}\n   → {ho_path}\n   → {man_path}")
    return 0


def _limitations(kept: list[CandidateCase], audits: dict[str, dict]) -> list[str]:
    """記錄已知局限（如 Generator 與 Auditor 同模型 → 提高人工抽檢；PRD §8）。"""
    lims: list[str] = [
        "Mock 分數非真實線上準確率；上線前須用真實 Gold 重定阈值（PRD §12）。"
    ]
    gen = {c.generator_model for c in kept if c.generator_model}
    aud = {a.get("auditor_model") for a in audits.values() if a.get("auditor_model")}
    if gen and aud and gen == aud:
        lims.append(
            "⚠️ Generator 與 Auditor 使用同一模型 snapshot；隔離性受限，應提高人工抽檢比例（PRD §8）。"
        )
    return lims


if __name__ == "__main__":
    raise SystemExit(main())

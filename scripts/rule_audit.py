#!/usr/bin/env python3
"""Rule 覆蓋率 + 品質稽核報表（Phase A · 純讀取，不改資料）。

兩份報表:
1. 靜態品質稽核 — 掃 config/ai_judge/rule_C-*.json 的 229 個 L3,對「厚判準」欄位
   (canon/allow/forbid/positive_cases/negative_cases/machine_clues) 評完整度,產弱判準
   優先補全清單。verdict / verdict_rules 屬正在移除的軸B,不列入評分(只在附註提示)。
2. 命中覆蓋率 — 對現有 judgments 舊資料的 data JSON 聚合 l3_code 命中次數,交叉出:
   - 從未命中的 L3(rule 有、資料無)
   - 弱判準卻仍被命中的 L3(高風險:判準弱但硬歸)
   - 資料出現但 rule 不存在的孤兒 l3_code(現有 rule 未覆蓋 → 需擴充)
   - l3_candidates top1↔top2 分數過近(判準區辨度不足)

DB 不可達時僅產出靜態品質稽核,覆蓋率段落標記 skipped(不視為錯誤)。

用法: python scripts/rule_audit.py  → 輸出 data/reports/rule_quality.{md,json}
                                        + data/reports/rule_coverage.{md,json}
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# repo 根:scripts/rule_audit.py → parents[1]
_ROOT = Path(__file__).resolve().parents[1]
_AI_JUDGE_DIR = _ROOT / "config" / "ai_judge"
_REPORTS_DIR = _ROOT / "data" / "reports"

# canon 判準本體視為「過短」的字元門檻(短於此幾乎無法承載一條可判的法典條文)
_CANON_MIN_LEN = 12
# 厚判準欄位權重(canon 為判準本體,權重最高;正反例次之;機器線索輔助)
# 刻意排除 verdict / verdict_rules —— 軸B 判決維度正在 Phase B 移除,不應計入品質分。
_FIELD_WEIGHTS: dict[str, float] = {
    "canon": 0.35,
    "allow": 0.15,
    "forbid": 0.15,
    "positive_cases": 0.15,
    "negative_cases": 0.12,
    "machine_clues": 0.08,
}


def _flatten_l3(rule_file: Path) -> list[dict[str, Any]]:
    """單一 rule_C-*.json → 其下所有 L3 葉節點(帶 L1/L2 上下文與 _meta 審核狀態)。

    Args:
        rule_file: config/ai_judge/rule_C-N.json 路徑。

    Returns:
        L3 dict 清單;每筆含 code/labels + 厚判準欄位 + thick_status(檔級審核標記)。
    """
    data = json.loads(rule_file.read_text(encoding="utf-8"))
    meta = data.get("_meta", {})
    # C-1 無 thick_fields_status(已人工審);C-2~C-7 標「②~⑦ AI 起草,待 PM 審核」
    thick_status = meta.get("thick_fields_status", "reviewed")
    tree = data.get("tree", [])
    if not tree:
        return []
    l1 = tree[0]
    l1_domain = l1.get("domain", "")
    l1_label = l1.get("label", "")
    out: list[dict[str, Any]] = []
    for l2 in l1.get("children", []):
        l2_code = l2.get("code", "")
        l2_label = l2.get("label", "")
        for l3 in l2.get("children", []):
            if l3.get("level") != 3:
                continue
            out.append(
                {
                    "code": l3.get("code", ""),
                    "l1_domain": l1_domain,
                    "l1_label": l1_label,
                    "l2_code": l2_code,
                    "l2_label": l2_label,
                    "l3_label": l3.get("label", ""),
                    "thick_status": thick_status,
                    # 厚判準原始值(供完整度評分)
                    "canon": l3.get("canon", ""),
                    "allow": l3.get("allow", []),
                    "forbid": l3.get("forbid", []),
                    "positive_cases": l3.get("positive_cases", []),
                    "negative_cases": l3.get("negative_cases", []),
                    "machine_clues": l3.get("machine_clues", []),
                }
            )
    return out


def _load_all_l3() -> list[dict[str, Any]]:
    """載入 7 個 rule 檔全部 L3(保檔名排序,C-1→C-7)。"""
    out: list[dict[str, Any]] = []
    for rule_file in sorted(_AI_JUDGE_DIR.glob("rule_C-*.json")):
        out.extend(_flatten_l3(rule_file))
    return out


def _field_filled(node: dict[str, Any], field: str) -> bool:
    """單一厚判準欄位是否「有效填寫」。

    canon 需非空且長度足(短於 _CANON_MIN_LEN 視為佔位);其餘 list 欄位需非空陣列。
    """
    val = node.get(field)
    if field == "canon":
        return isinstance(val, str) and len(val.strip()) >= _CANON_MIN_LEN
    return isinstance(val, list) and len(val) > 0


def _quality_score(node: dict[str, Any]) -> tuple[float, list[str]]:
    """L3 厚判準完整度加權分(0~1)+ 缺漏欄位清單。

    Returns:
        (score, missing_fields):score 為各欄權重加總;missing 為未有效填寫的欄位名。
    """
    score = 0.0
    missing: list[str] = []
    for field, weight in _FIELD_WEIGHTS.items():
        if _field_filled(node, field):
            score += weight
        else:
            missing.append(field)
    return round(score, 3), missing


def build_quality_report(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    """靜態品質稽核:逐 L3 評分 + 弱判準排序(分低者優先補)。"""
    rows: list[dict[str, Any]] = []
    for n in nodes:
        score, missing = _quality_score(n)
        rows.append(
            {
                "code": n["code"],
                "l1_domain": n["l1_domain"],
                "l3_label": n["l3_label"],
                "thick_status": n["thick_status"],
                "score": score,
                "missing": missing,
                "canon_len": len((n.get("canon") or "").strip()),
            }
        )
    rows.sort(key=lambda r: (r["score"], r["code"]))  # 分低優先
    by_domain: dict[str, dict[str, Any]] = {}
    for r in rows:
        d = by_domain.setdefault(r["l1_domain"], {"count": 0, "score_sum": 0.0})
        d["count"] += 1
        d["score_sum"] += r["score"]
    domain_summary = {
        dom: {"count": v["count"], "avg_score": round(v["score_sum"] / v["count"], 3)}
        for dom, v in by_domain.items()
    }
    weak = [r for r in rows if r["score"] < 0.7]  # 未達 0.7 視為弱判準,列補全清單
    return {
        "total_l3": len(rows),
        "domain_summary": domain_summary,
        "weak_count": len(weak),
        "weak_rules": weak,
        "all_rows": rows,
    }


def _fetch_l3_hits() -> dict[str, Any] | None:
    """從 judgments.data JSON 聚合 l3_code 命中次數 + top-3 區辨度樣本。

    Returns:
        {hits: {l3_code: count}, candidate_gaps: [...], total_judged: int}
        DB 不可達 → None(覆蓋率報表標 skipped)。
    """
    try:
        # 延後 import:靜態品質稽核不需 DB;避免 DB 未起時整支腳本掛掉
        sys.path.insert(0, str(_ROOT / "backend"))
        from app.core import tables as T  # noqa: PLC0415
        from sqlalchemy import select  # noqa: PLC0415

        hits: dict[str, int] = {}
        candidate_gaps: list[dict[str, Any]] = []
        total_judged = 0
        with T.get_engine().connect() as c:
            for (raw_data,) in c.execute(select(T.judgments.c.data)):
                if not raw_data:
                    continue
                try:
                    finding = json.loads(raw_data)
                except (ValueError, TypeError):
                    continue
                total_judged += 1
                code = finding.get("l3_code")
                if code:
                    hits[code] = hits.get(code, 0) + 1
                # top-3 候選:top1 與 top2 分數過近 → 判準區辨度不足
                cands = finding.get("l3_candidates") or []
                if len(cands) >= 2:
                    s1 = _cand_score(cands[0])
                    s2 = _cand_score(cands[1])
                    if s1 is not None and s2 is not None and (s1 - s2) < 0.1:
                        candidate_gaps.append(
                            {"l3_code": code, "top1": s1, "top2": s2, "gap": round(s1 - s2, 3)}
                        )
        return {"hits": hits, "candidate_gaps": candidate_gaps, "total_judged": total_judged}
    except Exception as exc:  # DB 未起 / 連線失敗:降級為 skipped,非致命
        print(f"[coverage] skipped — DB 不可達: {exc}", file=sys.stderr)
        return None


def _cand_score(cand: Any) -> float | None:
    """從 l3_candidates 元素取符合度分數(容忍 dict{score} 或 [code, score] 兩種形態)。"""
    if isinstance(cand, dict):
        v = cand.get("score")
        return float(v) if isinstance(v, (int, float)) else None
    if isinstance(cand, (list, tuple)) and len(cand) >= 2 and isinstance(cand[1], (int, float)):
        return float(cand[1])
    return None


def build_coverage_report(
    nodes: list[dict[str, Any]], quality: dict[str, Any]
) -> dict[str, Any] | None:
    """命中覆蓋率:交叉 rule L3 集合與 judgments 實際命中。"""
    hit_data = _fetch_l3_hits()
    if hit_data is None:
        return None
    hits: dict[str, int] = hit_data["hits"]
    rule_codes = {n["code"] for n in nodes}
    score_by_code = {r["code"]: r["score"] for r in quality["all_rows"]}

    never_hit = sorted(c for c in rule_codes if hits.get(c, 0) == 0)
    orphan_codes = sorted(c for c in hits if c not in rule_codes)  # 資料有、rule 無 → 需擴充
    weak_but_hit = sorted(
        (
            {"code": c, "hits": hits[c], "score": score_by_code.get(c, 0.0)}
            for c in hits
            if c in rule_codes and score_by_code.get(c, 1.0) < 0.7
        ),
        key=lambda r: (-r["hits"], r["score"]),
    )
    top_hit = sorted(hits.items(), key=lambda kv: -kv[1])[:30]
    return {
        "total_judged": hit_data["total_judged"],
        "distinct_l3_hit": len([c for c in hits if hits[c] > 0]),
        "total_l3_in_rules": len(rule_codes),
        "never_hit_count": len(never_hit),
        "never_hit": never_hit,
        "orphan_code_count": len(orphan_codes),
        "orphan_codes": orphan_codes,
        "weak_but_hit": weak_but_hit,
        "low_discrimination_count": len(hit_data["candidate_gaps"]),
        "top_hit": top_hit,
    }


def _write_quality_md(q: dict[str, Any]) -> str:
    """品質稽核 → markdown。"""
    lines = ["# Rule 品質靜態稽核報表", ""]
    lines.append(f"- L3 總數: **{q['total_l3']}**  ·  弱判準(score<0.7): **{q['weak_count']}**")
    lines.append("")
    lines.append("## 各域平均判準完整度")
    lines.append("")
    lines.append("| 域 | L3 數 | 平均分 |")
    lines.append("|---|---|---|")
    for dom, s in sorted(q["domain_summary"].items(), key=lambda kv: kv[1]["avg_score"]):
        lines.append(f"| {dom} | {s['count']} | {s['avg_score']} |")
    lines.append("")
    lines.append("## 弱判準補全優先清單(分低者優先)")
    lines.append("")
    lines.append("| L3 code | 域 | 名稱 | 分 | canon 長 | 缺漏欄位 | 審核狀態 |")
    lines.append("|---|---|---|---|---|---|---|")
    for r in q["weak_rules"]:
        miss = ", ".join(r["missing"]) or "-"
        lines.append(
            f"| {r['code']} | {r['l1_domain']} | {r['l3_label']} | {r['score']} "
            f"| {r['canon_len']} | {miss} | {r['thick_status']} |"
        )
    lines.append("")
    return "\n".join(lines)


def _write_coverage_md(cov: dict[str, Any] | None) -> str:
    """覆蓋率 → markdown(None → skipped 說明)。"""
    if cov is None:
        return "# Rule 命中覆蓋率報表\n\n> ⚠️ DB 不可達,覆蓋率段落 skipped。先 `./scripts/dev.sh` 起後端再跑。\n"
    lines = ["# Rule 命中覆蓋率報表", ""]
    lines.append(
        f"- 已判筆數: **{cov['total_judged']}**  ·  命中不重複 L3: "
        f"**{cov['distinct_l3_hit']}/{cov['total_l3_in_rules']}**"
    )
    lines.append(
        f"- 從未命中 L3: **{cov['never_hit_count']}**  ·  孤兒 code(rule 未覆蓋): "
        f"**{cov['orphan_code_count']}**  ·  低區辨度樣本: **{cov['low_discrimination_count']}**"
    )
    lines.append("")
    lines.append("## ⚠️ 需擴充:資料命中但 rule 不存在的孤兒 l3_code")
    lines.append("")
    lines.append(", ".join(cov["orphan_codes"]) or "(無)")
    lines.append("")
    lines.append("## ⚠️ 高風險:弱判準卻仍被命中(判準弱但硬歸,優先補)")
    lines.append("")
    lines.append("| L3 code | 命中數 | 品質分 |")
    lines.append("|---|---|---|")
    for r in cov["weak_but_hit"][:40]:
        lines.append(f"| {r['code']} | {r['hits']} | {r['score']} |")
    lines.append("")
    lines.append("## 從未命中的 L3(rule 有、資料無)")
    lines.append("")
    lines.append(", ".join(cov["never_hit"]) or "(無)")
    lines.append("")
    lines.append("## Top 30 命中 L3")
    lines.append("")
    lines.append("| L3 code | 命中數 |")
    lines.append("|---|---|")
    for code, n in cov["top_hit"]:
        lines.append(f"| {code} | {n} |")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    """產出兩份報表(md + json)到 data/reports/。"""
    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    nodes = _load_all_l3()
    quality = build_quality_report(nodes)
    coverage = build_coverage_report(nodes, quality)

    (_REPORTS_DIR / "rule_quality.md").write_text(_write_quality_md(quality), encoding="utf-8")
    (_REPORTS_DIR / "rule_quality.json").write_text(
        json.dumps(quality, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (_REPORTS_DIR / "rule_coverage.md").write_text(_write_coverage_md(coverage), encoding="utf-8")
    (_REPORTS_DIR / "rule_coverage.json").write_text(
        json.dumps(coverage, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"L3 總數 {quality['total_l3']} · 弱判準 {quality['weak_count']}")
    if coverage is None:
        print("覆蓋率: skipped(DB 不可達)")
    else:
        print(
            f"覆蓋率: 已判 {coverage['total_judged']} · 從未命中 {coverage['never_hit_count']}"
            f" · 孤兒 code {coverage['orphan_code_count']}"
        )
    print(f"報表輸出 → {_REPORTS_DIR}")


if __name__ == "__main__":
    main()

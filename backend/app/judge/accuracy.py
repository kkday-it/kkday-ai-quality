"""初判歸因準確度：label-free 自證底座（Cleanlab）+ 真值監督評估（true_label · sklearn）。

兩條路徑（`run()` 同時產出）：
- **label-free**（Cleanlab confident-learning）：無人工真值時的一致性體檢 + 可疑樣本人審清單。
  ⚠️ 侷限（循環論證）：以「模型自身 argmax = pseudo-label」＋ raw_confidence 派生 pred_probs 自證，
  屬內部一致性偵測非絕對準確率；系統性偏誤（整片同錯一類）無法揭露，與 rule_coverage 矛盾以人審為準。
- **真值監督**（`analyze_supervised`）：有 true_label 時算 L1 域歸因**真準確率** + 每類 P/R/F1 +
  最常誤判對，優先於 label-free 代理。用 sklearn.metrics；**不用 DeepEval**（遙測外洩 + GEval 需 key）。

輸入：judgments typed 判決欄（label-free 讀 l3_code/conf_raw；監督讀 l1_code/true_label）。
輸出：data/reports/accuracy.{md,json}（label-free）+ accuracy_supervised.{md,json}（監督）；
DB / cleanlab / sklearn 不可用或樣本不足時各自優雅降級為 skipped。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# repo 根：backend/app/judge/accuracy.py → parents[3]
_ROOT = Path(__file__).resolve().parents[3]
_REPORTS_DIR = _ROOT / "data" / "reports"

# 分析門檻：類別數 / 樣本數過低時 Cleanlab 統計無意義，直接 skip（避免假精確）。
_MIN_SAMPLES = 20
_MIN_CLASSES = 2
# 未提及類別的 pred_probs 基底機率（避免 0，Cleanlab 需每列為合法機率分佈）。
_EPSILON = 1e-6


def _load_attributed() -> list[dict[str, Any]] | None:
    """從 judgments.data 撈負向 attributed finding（有 l3_code + raw_confidence）。

    Returns:
        [{finding_id, ticket_id, l3_code, raw_confidence, candidates:{code:score}, l1_domain}]；
        DB 不可達 → None（報表標 skipped）。
    """
    try:
        sys.path.insert(0, str(_ROOT / "backend"))
        from sqlalchemy import select  # noqa: PLC0415

        from app.core import ai_judge  # noqa: PLC0415
        from app.core.db import tables as T  # noqa: PLC0415
        out: list[dict[str, Any]] = []
        with T.get_engine().connect() as c:
            jg = T.judgments
            # 攤平後判決欄皆 typed 欄，直接 select（l3_code / conf_raw / 關聯鍵）
            rows = c.execute(
                select(jg.c.finding_id, jg.c.source_id, jg.c.l3_code, jg.c.conf_raw)
                .where(jg.c.l3_code.isnot(None), jg.c.l3_code != "", jg.c.conf_raw.isnot(None))
            )
            for fid, sid, code, raw_conf in rows:
                # l3_candidates 於攤平時移除（實測全庫恆空，候選機制未落庫）→ candidates 恆空，行為不變
                node = ai_judge.l3_by_code(code) or {}
                out.append(
                    {
                        "finding_id": fid or "",
                        "ticket_id": sid or "",
                        "l3_code": code,
                        "raw_confidence": float(raw_conf),
                        "candidates": {},
                        "l1_domain": node.get("l1_domain", "") or "—",
                    }
                )
        return out
    except Exception as exc:  # noqa: BLE001  DB 未起 / 連線失敗 → skipped，非致命
        print(f"[accuracy] skipped — DB 不可達: {exc}", file=sys.stderr)
        return None


def _build_matrix(findings: list[dict[str, Any]]):
    """findings → (labels, pred_probs, observed_codes)；類別空間＝實際被預測到的 l3_code 集合。

    每列 pred_probs：pred code 放 raw_confidence、候選 code 放其 score（取 max），未提及類別填
    _EPSILON，最後 normalize 成機率分佈。pseudo-label＝pred code（noisy label 交 Cleanlab 檢驗）。
    類別空間限定「被預測到的 code」→ 每類至少 1 樣本，滿足 Cleanlab「各類皆須出現」前提。

    Returns:
        (labels: np.ndarray[int], pred_probs: np.ndarray[N,K], observed_codes: list[str])；
        類別數 < _MIN_CLASSES 或樣本 < _MIN_SAMPLES → None。
    """
    import numpy as np  # noqa: PLC0415  lazy：未裝 numpy/cleanlab 時上層降級

    observed_codes = sorted({f["l3_code"] for f in findings})
    if len(observed_codes) < _MIN_CLASSES or len(findings) < _MIN_SAMPLES:
        return None
    idx = {code: i for i, code in enumerate(observed_codes)}
    k = len(observed_codes)

    labels = np.empty(len(findings), dtype=int)
    pred_probs = np.full((len(findings), k), _EPSILON, dtype=float)
    for row, f in enumerate(findings):
        pi = idx[f["l3_code"]]
        labels[row] = pi
        pred_probs[row, pi] = max(f["raw_confidence"], _EPSILON)
        for cc, cs in f["candidates"].items():
            if cc in idx and cs > 0:
                j = idx[cc]
                pred_probs[row, j] = max(pred_probs[row, j], cs)
        pred_probs[row] /= pred_probs[row].sum()  # normalize 成合法機率分佈
    return labels, pred_probs, observed_codes


def analyze(findings: list[dict[str, Any]]) -> dict[str, Any]:
    """跑 Cleanlab：估標註問題率（label-free 準確度代理）+ 每樣本品質分 + 域/L3 聚合。

    Returns:
        {status, n, k, est_issue_count, proxy_accuracy, by_domain, worst_l3, low_quality_samples}；
        cleanlab/numpy 未裝或樣本不足 → {status: 'skipped', reason}。
    """
    try:
        import numpy as np  # noqa: PLC0415
        from cleanlab.filter import find_label_issues  # noqa: PLC0415
        from cleanlab.rank import get_label_quality_scores  # noqa: PLC0415
    except ImportError as exc:
        return {"status": "skipped", "reason": f"cleanlab/numpy 未安裝（pip install -e '.[accuracy]'）：{exc}"}

    built = _build_matrix(findings)
    if built is None:
        return {
            "status": "skipped",
            "reason": f"樣本/類別不足（需 ≥{_MIN_SAMPLES} 樣本、≥{_MIN_CLASSES} 類）：n={len(findings)}",
        }
    labels, pred_probs, observed_codes = built
    n = len(labels)

    # 可疑標註 mask（保守 filter_by=confident_learning）+ 每樣本品質分（低＝越可能錯）
    issue_mask = find_label_issues(labels, pred_probs, filter_by="confident_learning")
    quality = get_label_quality_scores(labels, pred_probs)
    est_issues = int(issue_mask.sum())
    proxy_accuracy = round(1.0 - est_issues / n, 4)

    # 域級聚合（品質分均值 + 疑問率）
    by_domain: dict[str, dict[str, Any]] = {}
    for i, f in enumerate(findings[:n]):
        d = by_domain.setdefault(f["l1_domain"], {"n": 0, "q_sum": 0.0, "issues": 0})
        d["n"] += 1
        d["q_sum"] += float(quality[i])
        d["issues"] += int(bool(issue_mask[i]))
    domain_summary = {
        dom: {
            "n": v["n"],
            "avg_quality": round(v["q_sum"] / v["n"], 3),
            "issue_rate": round(v["issues"] / v["n"], 3),
        }
        for dom, v in sorted(by_domain.items(), key=lambda kv: kv[1]["q_sum"] / kv[1]["n"])
    }

    # L3 級：品質分均值最低的 20 類（判準最可能誤歸的細項）
    l3_agg: dict[str, dict[str, Any]] = {}
    for i, f in enumerate(findings[:n]):
        a = l3_agg.setdefault(f["l3_code"], {"n": 0, "q_sum": 0.0})
        a["n"] += 1
        a["q_sum"] += float(quality[i])
    worst_l3 = sorted(
        ({"l3_code": c, "n": a["n"], "avg_quality": round(a["q_sum"] / a["n"], 3)}
         for c, a in l3_agg.items()),
        key=lambda r: r["avg_quality"],
    )[:20]

    # 低品質樣本人審清單（品質分升序 top 50；供人工覆核最可疑歸因）
    order = np.argsort(quality)
    low_quality = [
        {
            "finding_id": findings[i]["finding_id"],
            "ticket_id": findings[i]["ticket_id"],
            "l3_code": findings[i]["l3_code"],
            "raw_confidence": round(findings[i]["raw_confidence"], 3),
            "quality": round(float(quality[i]), 3),
            "is_issue": bool(issue_mask[i]),
        }
        for i in order[:50]
    ]

    return {
        "status": "ok",
        "n": n,
        "k": len(observed_codes),
        "est_issue_count": est_issues,
        "proxy_accuracy": proxy_accuracy,
        "by_domain": domain_summary,
        "worst_l3": worst_l3,
        "low_quality_samples": low_quality,
    }


def build_report() -> dict[str, Any]:
    """組報表資料：撈 attributed → analyze；DB 不可達回 {status: skipped}。"""
    findings = _load_attributed()
    if findings is None:
        return {"status": "skipped", "reason": "DB 不可達（先 ./scripts/dev/dev.sh 起後端）"}
    return analyze(findings)


_LIMITATION = (
    "> ⚠️ **侷限（循環論證）**：無人工真值，本報表以「模型自身 argmax = pseudo-label」自證，"
    "屬**內部一致性 / 可疑樣本偵測**，非絕對準確率。系統性偏誤（整片同錯一類）無法揭露；"
    "與 rule_coverage 矛盾時以人審為準。真值（true_label ≥ 200）到位後改走真校準。"
)


def _write_md(rep: dict[str, Any]) -> str:
    """報表 → markdown（頂部強制附侷限聲明）。"""
    lines = ["# 初判歸因 label-free 準確度報表（Cleanlab）", "", _LIMITATION, ""]
    if rep.get("status") != "ok":
        lines.append(f"> skipped — {rep.get('reason', '')}")
        lines.append("")
        return "\n".join(lines)
    lines.append(
        f"- 樣本數 **{rep['n']}** · 類別數 **{rep['k']}** · 估可疑標註 **{rep['est_issue_count']}**"
        f" · 一致性代理準確度 **{rep['proxy_accuracy']:.1%}**"
    )
    lines += ["", "## 各域一致性（品質分均值升序，低者最可疑）", "",
              "| 域 | 樣本 | 品質分均值 | 疑問率 |", "|---|---|---|---|"]
    for dom, s in rep["by_domain"].items():
        lines.append(f"| {dom} | {s['n']} | {s['avg_quality']} | {s['issue_rate']} |")
    lines += ["", "## 最可疑 L3（品質分均值最低 20 類）", "",
              "| L3 code | 樣本 | 品質分均值 |", "|---|---|---|"]
    for r in rep["worst_l3"]:
        lines.append(f"| {r['l3_code']} | {r['n']} | {r['avg_quality']} |")
    lines += ["", "## 低品質樣本人審清單（品質分最低 50 筆）", "",
              "| finding_id | L3 code | 原始信心 | 品質分 | 疑問 |", "|---|---|---|---|---|"]
    for r in rep["low_quality_samples"]:
        flag = "⚠️" if r["is_issue"] else ""
        lines.append(
            f"| {r['finding_id']} | {r['l3_code']} | {r['raw_confidence']} | {r['quality']} | {flag} |"
        )
    lines.append("")
    return "\n".join(lines)


def run() -> dict[str, Any]:
    """產出 label-free（accuracy.{md,json}）+ 真值監督（accuracy_supervised.{md,json}）兩份報表。

    Returns:
        {label_free, supervised} 兩報表 dict（供腳本印摘要 / CI 準確度閘門）。真值監督為 true_label
        到位後的**真準確率**，優先於 label-free 自證代理（後者有循環論證侷限，見 _LIMITATION）。
    """
    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    rep = build_report()
    (_REPORTS_DIR / "accuracy.md").write_text(_write_md(rep), encoding="utf-8")
    (_REPORTS_DIR / "accuracy.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    sup = supervised_report()
    (_REPORTS_DIR / "accuracy_supervised.md").write_text(_write_supervised_md(sup), encoding="utf-8")
    (_REPORTS_DIR / "accuracy_supervised.json").write_text(
        json.dumps(sup, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"label_free": rep, "supervised": sup}


# ── 真值監督評估（true_label；sklearn.metrics）──────────────────────────────────
# 補 label-free 自證的循環論證缺口：有人工 true_label 時，直接算 L1 域歸因的**真準確率**
# + 每類 P/R/F1 + 最常誤判對。用 sklearn.metrics（輕量、已於 .[accuracy] 具備）；**不用 DeepEval**
# （拉 posthog/sentry 遙測有外洩疑慮 + GEval 需 LLM key）。GEval 質性評判接 CI 待有 key 時另評。
_MIN_SUPERVISED = 30  # 監督評估最少標註樣本（少於此每類 support 過薄、指標不穩 → skip）


def _load_labeled_supervised() -> tuple[list[str], list[str]] | None:
    """撈 judgments 標註列 → (pred_l1_code 清單, true_label 清單)；DB 不可達回 None。

    監督真值＝人工 true_label（正確 L1 域 code，見 findings.update_finding_true_label）；預測＝l1_code。
    皆非空才納入（與 calibration._load_labeled 同來源，此處比對 code 而非二元 correct）。
    """
    try:
        from sqlalchemy import select

        from app.core.db import tables as T

        jg = T.judgments
        stmt = select(jg.c.l1_code, jg.c.true_label).where(
            jg.c.l1_code.isnot(None),
            jg.c.l1_code != "",
            jg.c.true_label.isnot(None),
            jg.c.true_label != "",
        )
        with T.get_engine().connect() as c:
            rows = c.execute(stmt).all()
    except Exception:  # noqa: BLE001  DB 未就緒 / 表缺 → 標 skipped
        return None
    return [r.l1_code for r in rows], [r.true_label for r in rows]


def analyze_supervised(pred: list[str], true: list[str]) -> dict[str, Any]:
    """真值監督評估：accuracy + macro P/R/F1 + 每類指標 + 最常誤判對（true→pred）。

    Args:
        pred: 模型預測 L1 域 code 清單。
        true: 對應人工真值 code 清單（等長）。

    Returns:
        {status, n, accuracy, macro_precision/recall/f1, per_class, top_confusions}；
        樣本不足 / sklearn 未裝 → {status: 'skipped', reason}。
    """
    n = len(pred)
    if n < _MIN_SUPERVISED:
        return {"status": "skipped", "reason": "insufficient_labels", "n": n, "min": _MIN_SUPERVISED}
    try:
        from sklearn.metrics import (
            accuracy_score,
            classification_report,
            confusion_matrix,
            precision_recall_fscore_support,
        )
    except ImportError as exc:
        return {"status": "skipped", "reason": f"sklearn 未安裝（pip install -e '.[accuracy]'）：{exc}"}

    labels = sorted(set(true) | set(pred))
    acc = float(accuracy_score(true, pred))
    p, r, f, _ = precision_recall_fscore_support(
        true, pred, labels=labels, average="macro", zero_division=0
    )
    report = classification_report(true, pred, labels=labels, output_dict=True, zero_division=0)
    per_class = {
        lbl: {
            "precision": round(report[lbl]["precision"], 3),
            "recall": round(report[lbl]["recall"], 3),
            "f1": round(report[lbl]["f1-score"], 3),
            "support": int(report[lbl]["support"]),
        }
        for lbl in labels
        if lbl in report
    }
    cm = confusion_matrix(true, pred, labels=labels)
    confusions = [
        {"true": labels[i], "pred": labels[j], "count": int(cm[i][j])}
        for i in range(len(labels))
        for j in range(len(labels))
        if i != j and cm[i][j] > 0
    ]
    confusions.sort(key=lambda x: x["count"], reverse=True)
    return {
        "status": "ok",
        "n": n,
        "accuracy": round(acc, 4),
        "macro_precision": round(float(p), 4),
        "macro_recall": round(float(r), 4),
        "macro_f1": round(float(f), 4),
        "per_class": per_class,
        "top_confusions": confusions[:15],
    }


def supervised_report() -> dict[str, Any]:
    """撈標註列 → analyze_supervised；DB 不可達回 {status: skipped}。"""
    data = _load_labeled_supervised()
    if data is None:
        return {"status": "skipped", "reason": "DB 不可達（先 ./scripts/dev/dev.sh 起後端）"}
    return analyze_supervised(*data)


def _write_supervised_md(rep: dict[str, Any]) -> str:
    """真值監督報表 → markdown。"""
    lines = ["# 初判歸因真值監督準確度報表（true_label · sklearn）", ""]
    if rep.get("status") != "ok":
        lines += [f"> skipped — {rep.get('reason', '')}（樣本 {rep.get('n', 0)}）", ""]
        return "\n".join(lines)
    lines.append(
        f"- 樣本 **{rep['n']}** · 準確率 **{rep['accuracy']:.1%}**"
        f" · macro P/R/F1 **{rep['macro_precision']:.1%} / {rep['macro_recall']:.1%} / {rep['macro_f1']:.1%}**"
    )
    lines += ["", "## 每類指標（L1 域）", "", "| 域 | precision | recall | f1 | support |", "|---|---|---|---|---|"]
    for lbl, s in rep["per_class"].items():
        lines.append(f"| {lbl} | {s['precision']} | {s['recall']} | {s['f1']} | {s['support']} |")
    lines += ["", "## 最常誤判對（true → pred）", "", "| 真值 | 誤判為 | 次數 |", "|---|---|---|"]
    for c in rep["top_confusions"]:
        lines.append(f"| {c['true']} | {c['pred']} | {c['count']} |")
    lines.append("")
    return "\n".join(lines)

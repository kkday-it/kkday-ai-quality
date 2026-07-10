"""真值監督評估（true_label · sklearn.metrics）：L1 域歸因真準確率 + 每類 P/R/F1 + 最常誤判對。

補 label-free 自證的循環論證缺口，優先於 labelfree 代理。用 sklearn.metrics；**不用 DeepEval**
（拉 posthog/sentry 遙測有外洩疑慮 + GEval 需 LLM key）。自 accuracy.py 拆出（行為不變）。
"""

from __future__ import annotations

from typing import Any

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
        return {
            "status": "skipped",
            "reason": "insufficient_labels",
            "n": n,
            "min": _MIN_SUPERVISED,
        }
    try:
        from sklearn.metrics import (
            accuracy_score,
            classification_report,
            confusion_matrix,
            precision_recall_fscore_support,
        )
    except ImportError as exc:
        return {
            "status": "skipped",
            "reason": f"sklearn 未安裝（pip install -e '.[accuracy]'）：{exc}",
        }

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
        return {"status": "skipped", "reason": "DB 不可達（先 ./start.sh 起後端）"}
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
    lines += [
        "",
        "## 每類指標（L1 域）",
        "",
        "| 域 | precision | recall | f1 | support |",
        "|---|---|---|---|---|",
    ]
    for lbl, s in rep["per_class"].items():
        lines.append(f"| {lbl} | {s['precision']} | {s['recall']} | {s['f1']} | {s['support']} |")
    lines += ["", "## 最常誤判對（true → pred）", "", "| 真值 | 誤判為 | 次數 |", "|---|---|---|"]
    for c in rep["top_confusions"]:
        lines.append(f"| {c['true']} | {c['pred']} | {c['count']} |")
    lines.append("")
    return "\n".join(lines)

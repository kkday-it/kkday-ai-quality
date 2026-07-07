"""多 model 聯合判決比較評估（model_votes + true_label；sklearn Cohen's κ）。

答 zhaokun「準確率判定方式（多模型比對）」：每 voter vs true_label 準確率 + 模型間一致性(Cohen's κ)
+ 聯合(多數決) vs 最佳單模型 + 重點易混淆域對（content↔supplier，kiki 指出）熱點。
自 accuracy.py 拆出（行為不變）。
"""

from __future__ import annotations

from typing import Any

from .supervised import _MIN_SUPERVISED

_CONFUSION_WATCH = ("content", "supplier")  # 重點監看的易混淆 L1 域對


def analyze_model_agreement(preds_by_model: dict[str, list[str]], true: list[str]) -> dict[str, Any]:
    """多 voter 的 L1 域預測 vs true_label 比較評估（ensemble 準確率判定核心）。

    Args:
        preds_by_model: {model 名: [每筆 L1 code]}，各清單與 true 等長。
        true: 對應人工真值 L1 code 清單。

    Returns:
        {status, n, models, per_model_accuracy, pairwise_kappa, ensemble_accuracy, best_single,
         ensemble_gain, watch_confusion}；樣本 < _MIN_SUPERVISED 或 model < 2 或 sklearn 缺 → skipped。
    """
    from collections import Counter

    models = sorted(preds_by_model)
    n = len(true)
    if n < _MIN_SUPERVISED or len(models) < 2:
        return {"status": "skipped", "reason": "insufficient_labels_or_models", "n": n, "models": models}
    import math

    try:
        from sklearn.metrics import accuracy_score, cohen_kappa_score
    except ImportError as exc:
        return {"status": "skipped", "reason": f"sklearn 未安裝（pip install -e '.[accuracy]'）：{exc}"}

    per_model_acc = {m: round(float(accuracy_score(true, preds_by_model[m])), 4) for m in models}

    def _kappa(x: list[str], y: list[str]) -> float:
        """Cohen's κ；常數預測（無變異）κ 未定義回 nan → 記 0.0（避免污染 JSON 且語義＝無資訊一致性）。"""
        k = float(cohen_kappa_score(x, y))
        return 0.0 if math.isnan(k) else round(k, 4)

    # 模型間一致性（pairwise Cohen's κ）：扣除隨機一致，類別不平衡下比 % agreement 更誠實
    pairwise = [
        {"a": models[i], "b": models[j], "kappa": _kappa(preds_by_model[models[i]], preds_by_model[models[j]])}
        for i in range(len(models))
        for j in range(i + 1, len(models))
    ]
    # 聯合＝逐筆 L1 眾數（多數決；平手取 Counter.most_common 首個，順序穩定）
    ensemble_pred = [Counter(preds_by_model[m][k] for m in models).most_common(1)[0][0] for k in range(n)]
    ensemble_acc = round(float(accuracy_score(true, ensemble_pred)), 4)
    best_model = max(models, key=lambda m: per_model_acc[m])
    best_acc = per_model_acc[best_model]
    # 重點易混淆域對熱點（用聯合 pred 對 true 雙向計數）
    watch = [
        {"true": a, "pred": b, "count": cnt}
        for a in _CONFUSION_WATCH
        for b in _CONFUSION_WATCH
        if a != b and (cnt := sum(1 for t, p in zip(true, ensemble_pred, strict=True) if t == a and p == b))
    ]
    return {
        "status": "ok",
        "n": n,
        "models": models,
        "per_model_accuracy": per_model_acc,
        "pairwise_kappa": pairwise,
        "ensemble_accuracy": ensemble_acc,
        "best_single": {"model": best_model, "accuracy": best_acc},
        "ensemble_gain": round(ensemble_acc - best_acc, 4),  # 聯合較最佳單模型的準確率增益（可能為負）
        "watch_confusion": watch,
    }


def _load_model_votes_labeled() -> tuple[dict[str, list[str]], list[str]] | None:
    """撈 judgments 有 model_votes + true_label 的列 → (preds_by_model, true)；無 / DB 不可達回 None。

    model_votes＝ensemble 各 voter 攤平票 [{model, l1_code, …}]；每筆取各 voter 的 l1_code。僅納入
    「所有共同 voter 都有票」的筆（pairwise κ 需各 model 清單等長）。
    """
    try:
        from sqlalchemy import select

        from app.core.db import tables as T

        jg = T.judgments
        stmt = select(jg.c.model_votes, jg.c.true_label).where(
            jg.c.model_votes.isnot(None),
            jg.c.true_label.isnot(None),
            jg.c.true_label != "",
        )
        with T.get_engine().connect() as c:
            rows = c.execute(stmt).all()
    except Exception:  # noqa: BLE001  DB 未就緒 / 表缺 model_votes → 標 skipped
        return None

    per_finding: list[tuple[dict[str, str], str]] = []
    all_models: set[str] = set()
    for votes, true in rows:
        d = {v["model"]: v["l1_code"] for v in (votes or []) if v.get("model") and v.get("l1_code")}
        if d:
            per_finding.append((d, true))
            all_models |= set(d)
    if not per_finding:
        return None

    models = sorted(all_models)
    preds_by_model: dict[str, list[str]] = {m: [] for m in models}
    true_out: list[str] = []
    for d, true in per_finding:
        if all(m in d for m in models):  # 僅所有 voter 都有票的筆（等長對齊）
            for m in models:
                preds_by_model[m].append(d[m])
            true_out.append(true)
    return (preds_by_model, true_out) if true_out else None


def ensemble_report() -> dict[str, Any]:
    """撈 model_votes 標註列 → analyze_model_agreement；DB 不可達 / 無 ensemble 資料回 skipped。"""
    data = _load_model_votes_labeled()
    if data is None:
        return {"status": "skipped", "reason": "無 ensemble model_votes + true_label 資料（尚未跑聯合判決或未標真值）"}
    return analyze_model_agreement(*data)


def _write_ensemble_md(rep: dict[str, Any]) -> str:
    """多 model 聯合判決比較報表 → markdown。"""
    lines = ["# 多 model 聯合判決比較報表（model_votes · true_label · Cohen's κ）", ""]
    if rep.get("status") != "ok":
        lines += [f"> skipped — {rep.get('reason', '')}", ""]
        return "\n".join(lines)
    lines += [
        f"- 樣本 **{rep['n']}** · 參與 model：{'、'.join(rep['models'])}",
        f"- **聯合(多數決) 準確率 {rep['ensemble_accuracy']:.1%}** vs 最佳單模型 "
        f"{rep['best_single']['model']} {rep['best_single']['accuracy']:.1%}"
        f"（增益 {rep['ensemble_gain']:+.1%}）",
        "",
        "## 每 model vs true_label 準確率",
        "",
        "| model | 準確率 |",
        "|---|---|",
    ]
    for m, acc in rep["per_model_accuracy"].items():
        lines.append(f"| {m} | {acc:.1%} |")
    lines += ["", "## 模型間一致性（pairwise Cohen's κ）", "", "| model A | model B | κ |", "|---|---|---|"]
    for p in rep["pairwise_kappa"]:
        lines.append(f"| {p['a']} | {p['b']} | {p['kappa']} |")
    if rep["watch_confusion"]:
        lines += ["", "## 重點易混淆域熱點（聯合 pred；content↔supplier）", "", "| 真值 | 誤判為 | 次數 |", "|---|---|---|"]
        for c in rep["watch_confusion"]:
            lines.append(f"| {c['true']} | {c['pred']} | {c['count']} |")
    lines.append("")
    return "\n".join(lines)

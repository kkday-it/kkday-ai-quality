"""域路由器離線訓練（升級計畫 P3）——以歷史初判訓練 embedding 候選域分類器。

原理：attributions 的 (source, source_id) 級歷史初判＝免費標註（各案命中哪些 L1 域）；
text → OpenAI embedding → 每域一個 LogisticRegression（one-vs-rest, class_weight=balanced）。
閾值以「holdout recall ≥ --target-recall（預設 0.995）」為硬約束選最鬆可剪點——路由任務是
**別漏域**（高召回），省多少（precision/prune-rate）是次要結果。

產出：
- data/router/weights.json：runtime 權重（app/judge/domain_router.py 讀；純 python 內積推論）
- data/reports/router_training_<日期>.md：per-domain recall/precision/剪枝率 + always_on 建議
  ——recall 不達標或正樣本過少（< --min-positives）的域**不給閾值**（runtime 視同 always_on 恆跑）。

僅用負/中立案（prejudge.json/verdict.json polarity_gate.attribute_when 才會 fan-out 的傾向）；零域案
（全域棄權）為全域負樣本。訓練資料會隨初判累積成長，建議每月或大批新判後重訓。

依賴：scikit-learn（backend [dev] extras；容器內 pip install -e '.[dev]'）。
用法（scripts/ 未掛載容器，先 docker cp）：
    docker cp scripts/tools/train_domain_router.py kkday-ai-quality-backend:/app/scripts/tools/
    docker exec kkday-ai-quality-backend python /app/scripts/tools/train_domain_router.py \\
        --user alvin.bian@kkday.com

⚠️ 成本：embedding 全量（~1.6k 案 × ~200 tokens）≈ $0.01 級，可忽略。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "backend"))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from app.core import db  # noqa: E402
from app.core import settings as app_settings  # noqa: E402
from app.core.db import tables as T  # noqa: E402
from app.core.paths import DATA_DIR, REPORTS_DIR  # noqa: E402
from app.judge import prejudge, prompt_source  # noqa: E402
from app.judge.llm import client  # noqa: E402
from app.judge.prompt_eval import _build_sandbox_item  # noqa: E402
from sqlalchemy import text  # noqa: E402

WEIGHTS_PATH = DATA_DIR / "router" / "weights.json"
_EMBED_BATCH = 128  # embeddings API 單批筆數（上限 2048；保守分批）


def _md5pct(key: str) -> int:
    """md5 → 0-99（穩定 holdout 切分：同案永遠落同一側，重訓可比）。"""
    return int(hashlib.md5(key.encode()).hexdigest()[:8], 16) % 100  # noqa: S324


def _load_cases() -> list[dict]:
    """負/中立已初判案 → [{source, source_id, domains: set[str]}]（零域案＝全域負樣本）。"""
    when = tuple(prejudge._attribute_when())
    with T.get_engine().connect() as c:
        rows = c.execute(
            text(
                "SELECT source, source_id, "
                "array_agg(DISTINCT l1_code) FILTER (WHERE l1_code IS NOT NULL AND l1_code<>'') AS doms "
                "FROM attributions WHERE polarity = ANY(:when) GROUP BY source, source_id"
            ),
            {"when": list(when)},
        ).all()
    return [
        {"source": s, "source_id": str(i), "domains": set(d or [])} for s, i, d in rows
    ]


def _embed_texts(texts: list[str], model: str) -> list[list[float]]:
    """批量 embedding（直用 SDK client；離線工具不經 embed_one 的單筆介面）。"""
    cfg = client._resolve()
    cli = client._get_client(cfg["token"], cfg["base_url"])
    out: list[list[float]] = []
    for i in range(0, len(texts), _EMBED_BATCH):
        chunk = [t[:8000] or " " for t in texts[i : i + _EMBED_BATCH]]
        resp = cli.embeddings.create(model=model, input=chunk)
        out += [list(d.embedding) for d in resp.data]
        print(f"  … embedding {min(i + _EMBED_BATCH, len(texts))}/{len(texts)}")
    return out


def _pick_threshold(y_true: list[int], probs: list[float], target_recall: float) -> float | None:
    """在 holdout 上選「recall ≥ target 的最大閾值」（最鬆可剪點）；正樣本 0 回 None。"""
    pos = [p for p, y in zip(probs, y_true, strict=True) if y]
    if not pos:
        return None
    pos.sort()
    # 允許漏掉的正樣本數（floor）；閾值取第 k 小的正樣本機率再往下貼一點（<= 該樣本仍召回）
    k = int(len(pos) * (1.0 - target_recall))
    return max(0.0, pos[k] - 1e-9)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--user", required=True, help="user_settings token 來源（email）")
    ap.add_argument("--holdout", type=int, default=20, help="holdout 百分比（預設 20）")
    ap.add_argument("--target-recall", type=float, default=0.995, help="per-domain 召回閘門")
    ap.add_argument("--min-positives", type=int, default=30, help="低於此正樣本數不給閾值（always_on）")
    ap.add_argument("--exclude", default="", help="排除清單 json（{items:[{source,source_id}]}；評測集防洩漏用）")
    ap.add_argument("--provider", default="", help="覆寫供應商連線（空＝prejudge 功能區默認）")
    args = ap.parse_args()

    from sklearn.linear_model import LogisticRegression  # 重依賴 lazy（[dev] extras）

    u = db.get_user_by_email(args.user)
    if not u:
        raise SystemExit(f"❌ 找不到 user：{args.user}")
    app_settings.set_current(
        app_settings.effective_llm_dict(
            app_settings.load_settings(), area="prejudge", overrides={"provider": args.provider or None}
        )
    )
    if client.is_stub():
        raise SystemExit("❌ stub 模式（無 LLM token）無法產 embedding")
    router_cfg = prejudge._prejudge_cfg().get("domain_router") or {}
    embed_model = str(router_cfg.get("embedding_model") or "")
    if not embed_model:
        raise SystemExit("❌ prejudge.json/verdict.json prejudge.domain_router.embedding_model 未設定")

    cases = _load_cases()
    if args.exclude:
        # 評測集防洩漏：排除即將用於 A/B 對比的案，讓路由對它們是「沒見過的」
        excl = json.load(open(args.exclude, encoding="utf-8"))
        keys = {f"{it['source']}:{it['source_id']}" for it in excl.get("items", [])}
        before = len(cases)
        cases = [c for c in cases if f"{c['source']}:{c['source_id']}" not in keys]
        print(f"▶ 排除評測集 {before - len(cases)} 案（--exclude {args.exclude}）")
    print(f"▶ 訓練資料 {len(cases)} 案（負/中立已初判）· embedding={embed_model}")
    texts = []
    for cs in cases:
        item = _build_sandbox_item(cs["source"], cs["source_id"])
        texts.append(prejudge._text_of(item))
    vecs = _embed_texts(texts, embed_model)
    dim = len(vecs[0])

    is_hold = [_md5pct(f"{c['source']}:{c['source_id']}") < args.holdout for c in cases]
    # 域詞彙表鐵律：用域機器值（content/supplier…＝attributions.l1_code 同詞彙表），勿用 C-x 碼——
    # 否則 `dom in c["domains"]` 恆 False → 全域零正樣本 → 全 always_on，路由形同虛設。
    domains = sorted(
        {prompt_source._domain_of(pid) for pid in prompt_source.DOMAIN_PROMPT_IDS}
        - {""}
    )
    out_domains: dict[str, dict] = {}
    report_rows: list[str] = []
    hold_candidate_counts = [0] * sum(is_hold)

    for dom in domains:
        y = [1 if dom in c["domains"] else 0 for c in cases]
        x_tr = [v for v, h in zip(vecs, is_hold, strict=True) if not h]
        y_tr = [t for t, h in zip(y, is_hold, strict=True) if not h]
        x_ho = [v for v, h in zip(vecs, is_hold, strict=True) if h]
        y_ho = [t for t, h in zip(y, is_hold, strict=True) if h]
        n_pos = sum(y)
        entry: dict = {"coef": [], "intercept": 0.0, "threshold": None, "holdout": {}}
        if n_pos < args.min_positives or sum(y_tr) == 0 or len(set(y_tr)) < 2:
            # 正樣本過少：訓練不可靠 → 不給閾值（runtime 視同 always_on 恆跑），誠實標注
            report_rows.append(f"| {dom} | {n_pos} | — | — | — | always_on（正樣本不足） |")
            out_domains[dom] = entry
            for i in range(len(hold_candidate_counts)):
                hold_candidate_counts[i] += 1
            continue
        clf = LogisticRegression(max_iter=2000, class_weight="balanced")
        clf.fit(x_tr, y_tr)
        probs_ho = [float(p) for p in clf.predict_proba(x_ho)[:, 1]]
        thr = _pick_threshold(y_ho, probs_ho, args.target_recall)
        picked = [p >= thr for p in probs_ho] if thr is not None else [True] * len(probs_ho)
        tp = sum(1 for p, t in zip(picked, y_ho, strict=True) if p and t)
        recall = tp / sum(y_ho) if sum(y_ho) else None
        precision = tp / sum(picked) if sum(picked) else None
        prune = 1.0 - (sum(picked) / len(picked)) if picked else 0.0
        ok = recall is not None and recall >= args.target_recall
        entry = {
            "coef": [round(float(w), 6) for w in clf.coef_[0]],
            "intercept": round(float(clf.intercept_[0]), 6),
            # recall 未達標不給閾值 → runtime 恆跑該域（寧不剪，不冒漏域險）
            "threshold": round(thr, 6) if (ok and thr is not None) else None,
            "holdout": {
                "positives": int(sum(y_ho)),
                "recall": round(recall, 4) if recall is not None else None,
                "precision": round(precision, 4) if precision is not None else None,
                "prune_rate": round(prune, 4),
            },
        }
        for i, p in enumerate(picked if entry["threshold"] is not None else [True] * len(picked)):
            hold_candidate_counts[i] += 1 if p else 0
        verdict = "可剪" if entry["threshold"] is not None else "always_on（recall 未達標）"
        report_rows.append(
            f"| {dom} | {n_pos} | {entry['holdout']['recall']} | "
            f"{entry['holdout']['precision']} | {entry['holdout']['prune_rate']} | {verdict} |"
        )
        out_domains[dom] = entry

    avg_domains = (
        sum(hold_candidate_counts) / len(hold_candidate_counts) if hold_candidate_counts else 6.0
    )
    WEIGHTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    WEIGHTS_PATH.write_text(
        json.dumps(
            {
                "version": 1,
                "trained_at": datetime.now(timezone.utc).isoformat(),
                "embedding_model": embed_model,
                "dim": dim,
                "n_cases": len(cases),
                "holdout_pct": args.holdout,
                "target_recall": args.target_recall,
                "domains": out_domains,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    rp = REPORTS_DIR / f"router_training_{stamp}.md"
    rp.write_text(
        "\n".join(
            [
                "# 域路由訓練報告",
                "",
                f"> {datetime.now(timezone.utc).isoformat()} · {len(cases)} 案 · "
                f"holdout {args.holdout}% · 召回閘門 {args.target_recall} · embedding={embed_model}",
                "",
                "| 域 | 正樣本 | holdout recall | precision | 剪枝率 | 判定 |",
                "|---|---|---|---|---|---|",
                *report_rows,
                "",
                f"**holdout 平均候選域數 ≈ {avg_domains:.2f} / 6**（越低省越多；always_on 域恆計 1）",
                "",
                "上線閘門：可剪域 recall ≥ 目標且 eval_equivalence 金標集等價過閘後，"
                "將 prejudge.json/verdict.json prejudge.domain_router.enabled 設 true（建議先 shadow_rate 觀察）。",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"✅ 權重 → {WEIGHTS_PATH}")
    print(f"✅ 報告 → {rp}（holdout 平均候選域 {avg_domains:.2f}/6）")


if __name__ == "__main__":
    main()

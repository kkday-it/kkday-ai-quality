"""管線等價性閘門 harness（升級計畫 P0）——證明改動後「情緒傾向判定與歸類的準確性和數量不變」。

方法論（誠實等價判準）：LLM 天生非決定性，同管線雙跑也非 100% 一致 → 先以當前管線在固定金標集上
**雙跑**算出「自我一致性噪音地板」，之後每個改動（域路由 / effort 降檔 / cache_key…）跑同一金標集，
各指標 ≥ 地板 − 1pp 才視為等價過閘。指標純函式 SSOT＝app.judge.prompt_eval.compute_equivalence_metrics。

金標集：attributions 依 (source × polarity) 分層、md5(source:source_id) 穩定排序抽樣（跨 run 可比、
不受 DB 序影響），固定清單存 data/eval/golden_set.json。

跑初判走 **production 同一條路**（prejudge.to_findings，非診斷路徑），不落庫、不動 attributions；
強制關 exact-cache 讀取（否則第二跑全命中快取＝地板假 100%）。LLM token 走全項目共享設定。

用法（scripts/ 未掛載容器，先 docker cp）：
    docker cp scripts/tools/eval_equivalence.py kkday-ai-quality-backend:/app/scripts/tools/
    # 1) 建金標集（一次性；重建會改變樣本，舊 run 不可比）
    docker exec kkday-ai-quality-backend python /app/scripts/tools/eval_equivalence.py \\
        --build-golden --n 700
    # 2) 基線雙跑（噪音地板）
    docker exec kkday-ai-quality-backend python /app/scripts/tools/eval_equivalence.py \\
        --run base1
    docker exec kkday-ai-quality-backend python /app/scripts/tools/eval_equivalence.py \\
        --run base2
    docker exec kkday-ai-quality-backend python /app/scripts/tools/eval_equivalence.py \\
        --compare base1 base2          # → 地板報告
    # 3) 改動後跑一次、對基線比
    docker exec kkday-ai-quality-backend python /app/scripts/tools/eval_equivalence.py \\
        --run router_on
    docker exec kkday-ai-quality-backend python /app/scripts/tools/eval_equivalence.py \\
        --compare base1 router_on      # 各指標 ≥ 地板 − 1pp 才過閘

⚠️ 成本：每 run 對金標集全量真打 LLM（負/中立筆走六域 fan-out）；700 筆 @gpt-5-mini 約 $8–12。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from datetime import datetime, timezone

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "backend"))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from app.core import settings as app_settings  # noqa: E402
from app.core.db import tables as T  # noqa: E402
from app.core.paths import DATA_DIR, REPORTS_DIR  # noqa: E402
from app.judge import prejudge  # noqa: E402
from app.judge.llm import client  # noqa: E402
from app.judge.prompt_eval import _build_sandbox_item, compute_equivalence_metrics  # noqa: E402
from sqlalchemy import text  # noqa: E402

EVAL_DIR = DATA_DIR / "eval"
GOLDEN_PATH = EVAL_DIR / "golden_set.json"
RUNS_DIR = EVAL_DIR / "runs"


def _md5(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest()  # noqa: S324  非安全用途：穩定抽樣排序鍵


# ─────────────────────────── 金標集 ───────────────────────────
def build_golden(n: int) -> None:
    """分層抽樣建金標集：strata＝(source, polarity)，比例配額、md5 穩定排序取前 k。"""
    with T.get_engine().connect() as c:
        rows = c.execute(
            text(
                "SELECT source, source_id, max(polarity) AS polarity "
                "FROM attributions WHERE polarity IS NOT NULL GROUP BY source, source_id"
            )
        ).all()
    strata: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for src, sid, pol in rows:
        strata.setdefault((src, pol), []).append((src, str(sid)))
    total = sum(len(v) for v in strata.values())
    if not total:
        raise SystemExit("❌ attributions 無已初判資料，無法建金標集")
    items: list[dict] = []
    for (_src, pol), members in sorted(strata.items()):
        k = max(1, round(n * len(members) / total))  # 比例配額（每層至少 1）
        members.sort(key=lambda m: _md5(f"{m[0]}:{m[1]}"))  # 跨 run 穩定
        items += [{"source": s, "source_id": i, "polarity": pol} for s, i in members[:k]]
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    GOLDEN_PATH.write_text(
        json.dumps(
            {
                "created_at": datetime.now(timezone.utc).isoformat(),
                "n": len(items),
                "strata": {f"{s}:{p}": len(m) for (s, p), m in sorted(strata.items())},
                "items": items,
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"✅ 金標集 {len(items)} 筆 → {GOLDEN_PATH}")


# ─────────────────────────── 跑一輪 ───────────────────────────
def _summarize(findings: list) -> dict:
    """TicketFinding 清單 → 等價比對摘要（compute_equivalence_metrics 的輸入形狀）。"""
    facets = sorted(
        {(f.l1_domain_code, f.l2_code) for f in findings if f.l1_domain_code or f.l2_code}
    )
    primary = next(
        ([f.l1_domain_code, f.l2_code] for f in findings if getattr(f, "is_primary", False)),
        None,
    )
    f0 = findings[0] if findings else None
    return {
        "polarity": getattr(f0, "polarity", None),
        "sentiment": getattr(f0, "sentiment_score", None),
        "n_findings": len(findings),
        "facets": [list(t) for t in facets],
        "primary": primary,
    }


def run_once(tag: str, workers: int) -> None:
    """金標集全量走 production 初判路徑（to_findings；不落庫），結果存 runs/<tag>.json。"""
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    eff = app_settings.effective_llm_dict(app_settings.load_settings(), area="prejudge")
    app_settings.set_current(eff)
    if client.is_stub():
        raise SystemExit("❌ stub 模式（無 LLM token）不能量測等價性")
    client.set_llm_cache_read(False)  # 關快取讀取：雙跑必須真打，否則地板假 100%
    model = eff.get("model", "")
    items = golden["items"]
    print(f"▶ run={tag} model={model} n={len(items)} workers={workers}（exact-cache 讀取已關）")

    def _one(entry: dict) -> tuple[str, dict | None]:
        key = f"{entry['source']}:{entry['source_id']}"
        try:
            item = _build_sandbox_item(entry["source"], entry["source_id"])
            fs = prejudge.to_findings(item, model=model)
            return key, _summarize(fs)
        except Exception as e:  # noqa: BLE001  單筆失敗不毀整 run（報告記 null，比對時剔除）
            print(f"  ⚠️ {key} 失敗：{e}")
            return key, None

    results: dict[str, dict | None] = {}
    ctxs = [copy_context() for _ in items]
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(ctx.run, _one, it) for ctx, it in zip(ctxs, items, strict=True)]
        for i, fut in enumerate(futs, 1):
            key, summary = fut.result()
            results[key] = summary
            if i % 50 == 0:
                print(f"  … {i}/{len(items)}")
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    out = RUNS_DIR / f"{tag}.json"
    out.write_text(
        json.dumps(
            {
                "tag": tag,
                "model": model,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "golden_created_at": golden["created_at"],
                "items": results,
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    ok = sum(1 for v in results.values() if v is not None)
    print(f"✅ run={tag} 完成 {ok}/{len(items)} → {out}")


# ─────────────────────────── 比對 ───────────────────────────
def compare(tag_a: str, tag_b: str) -> None:
    """兩 run 逐筆配對 → 等價指標 + Markdown 報告（data/reports/）。"""
    a = json.loads((RUNS_DIR / f"{tag_a}.json").read_text(encoding="utf-8"))
    b = json.loads((RUNS_DIR / f"{tag_b}.json").read_text(encoding="utf-8"))
    if a.get("golden_created_at") != b.get("golden_created_at"):
        print("⚠️ 兩 run 的金標集版本不同——結果不可比，請確認未重建 golden_set")
    keys = sorted(set(a["items"]) & set(b["items"]))
    records = [
        {"a": a["items"][k], "b": b["items"][k]}
        for k in keys
        if a["items"][k] is not None and b["items"][k] is not None
    ]
    overall = compute_equivalence_metrics(records)
    by_pol: dict[str, dict] = {}
    for pol in ("positive", "negative", "neutral"):
        sub = [r for r in records if r["a"].get("polarity") == pol]
        if sub:
            by_pol[pol] = compute_equivalence_metrics(sub)

    lines = [
        f"# 等價性比對：{tag_a} vs {tag_b}",
        "",
        f"> 產出：{datetime.now(timezone.utc).isoformat()} · model A={a.get('model')} / "
        f"B={b.get('model')} · 可比 {len(records)}/{len(keys)} 筆",
        "",
        "| 指標 | overall | " + " | ".join(by_pol) + " |",
        "|---|---|" + "---|" * len(by_pol),
    ]
    for m in (
        "polarity_agree",
        "sentiment_agree",
        "count_equal",
        "count_mae",
        "facet_jaccard_mean",
        "primary_agree",
    ):
        row = [str(overall.get(m))] + [str(by_pol[p].get(m)) for p in by_pol]
        lines.append(f"| {m} | " + " | ".join(row) + " |")
    lines += [
        "",
        "判讀：基線雙跑（同管線）之結果＝噪音地板；改動 run 各指標 ≥ 地板 − 0.01 即等價過閘。",
    ]
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    rp = REPORTS_DIR / f"equivalence_{tag_a}_vs_{tag_b}.md"
    rp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"overall": overall, "by_polarity": by_pol}, ensure_ascii=False, indent=2))
    print(f"✅ 報告 → {rp}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--build-golden", action="store_true", help="建金標集（分層 md5 穩定抽樣）")
    ap.add_argument("--n", type=int, default=700, help="金標集目標筆數（預設 700）")
    ap.add_argument("--run", metavar="TAG", help="跑金標集全量並存結果（runs/<TAG>.json）")
    ap.add_argument("--workers", type=int, default=8, help="並發 worker 數（預設 8）")
    ap.add_argument("--compare", nargs=2, metavar=("TAG_A", "TAG_B"), help="比對兩 run")
    args = ap.parse_args()
    if args.build_golden:
        build_golden(args.n)
    elif args.run:
        run_once(args.run, args.workers)
    elif args.compare:
        compare(*args.compare)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()

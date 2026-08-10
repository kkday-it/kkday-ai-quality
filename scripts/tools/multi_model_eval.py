#!/usr/bin/env python3
"""多模型準確度評測——逐則跑指定模型收集歸因（唯讀：不寫 attributions）。

用於比較不同 LLM（字節 / Gemini / Claude…）在同一評測集上的初判準確率。唯讀配方（to_findings
收集、ThreadPool+copy_context 繼承 LLM 配置、cache_read=False、stub 拒跑），特點：
① 額外收集 sentiment_score（情緒分準確度用）② 以 --config-id 明確選某一 LLM 配置切換模型
③ 評測集為「有外部 free_tag ground truth 且已初判」的 reviews（供對比外部評論系統）。

兩模式：
- --build-set：建評測集 JSON（免 token）。預設該週有外部 free_tag 且已初判的 reviews。
- --run --config-id <id>：以該配置逐則重新初判評測集，收集每則 sentiment/polarity/歸因。

用法（backend venv）：
    cd backend
    .venv/bin/python ../scripts/tools/multi_model_eval.py --build-set \
        --date-from 2026-06-30 --date-to 2026-07-09 --out ../tmp/multi_model/evalset.json
    .venv/bin/python ../scripts/tools/multi_model_eval.py \
        --eval-set ../tmp/multi_model/evalset.json --config-id <字節/Gemini config id> \
        --out ../tmp/multi_model/bytedance.json [--limit 6]
"""

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import copy_context

# 讓腳本能 import backend 的 app 套件（不論從何處執行）
_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "backend"))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from sqlalchemy import select  # noqa: E402

from app.core import db  # noqa: E402
from app.core import settings as app_settings  # noqa: E402
from app.core.config import env  # noqa: E402
from app.core.db import tables as T  # noqa: E402
from app.judge.llm import client  # noqa: E402

_SOURCE = "reviews"
_FETCH_CHUNK = 200


def _build_evalset(date_from: str, date_to: str, judged_only: bool) -> list[dict]:
    """建評測集：該日期窗內有外部 free_tag ground truth（可選：且已初判）的 reviews。

    每則收集對比所需 ground truth：星等（rec_scores，用戶真實評分）、外部 sentiment、外部 free_tag。
    完整源列於 run 階段以 get_items_by_ids 依 rec_oid 取回（供 to_findings normalize）。
    """
    # 走 Table 物件而非 SQL 字串：DB 表名／欄名已與 Python key 脫鉤（`Column(規範名, key=原名)`），
    # 手寫字串會在下次改名時再度失效；由 metadata 產 SQL 才能自動跟上。
    r = T.reviews
    a = T.attributions
    stmt = select(r.c.rec_oid, r.c.rec_scores, r.c.sentiment, r.c.free_tag).distinct()
    if judged_only:
        stmt = stmt.join(a, (a.c.source == _SOURCE) & (a.c.source_id == r.c.rec_oid))
    stmt = stmt.where(
        r.c.free_tag.is_not(None),
        r.c.free_tag.not_in(("", "[]", "null")),
        r.c.create_date >= date_from,
        r.c.create_date < date_to,
    ).order_by(r.c.rec_oid)
    with T.get_engine().connect() as c:
        rows = c.execute(stmt).all()
    return [
        {
            "rec_oid": str(rec_oid),
            "star": star,  # 星等（用戶真實評分，1-5 字串）
            "ext_sentiment": sent,  # 外部評論系統情緒分 1-5
            "ext_free_tag": ftag,  # 外部 free_tag JSON 字串（原樣，計分時 parse）
        }
        for rec_oid, star, sent, ftag in rows
    ]


def _judge_one(item: dict, model: str) -> dict:
    """單則重新初判（唯讀）：normalize（複製 prejudge_batch._work_one 配方）→ to_findings → 收集。"""
    from app.core import source_mapping as _srcmap
    from app.core.db import source_registry as _reg
    from app.judge import prejudge

    spec = _reg.spec_for(_SOURCE)
    canon = _srcmap.normalize_row(_SOURCE, item) if _SOURCE in _srcmap.sources() else {}
    source_id = str(item.get(spec.natural_key) or "") if spec else ""
    client.set_usage_context({"job_id": "multi_model_eval", "source": _SOURCE, "source_id": source_id})
    norm = dict(item)
    norm["source"] = _SOURCE
    norm["source_id"] = source_id
    norm["content"] = canon.get("content") or ""
    norm["prod_oid"] = canon.get("prod_oid") or ""
    norm["order_oid"] = canon.get("order_oid") or ""
    norm["raw"] = item
    findings = prejudge.to_findings(norm, model=model)
    # sentiment/polarity 為評論級（各歸因一致）：取首個非 0 sentiment、primary（或首個）polarity
    sentiment = next((f.sentiment_score for f in findings if f.sentiment_score), 0)
    primary = next((f for f in findings if f.is_primary), findings[0] if findings else None)
    return {
        "rec_oid": source_id,
        "sentiment": sentiment,
        "polarity": primary.polarity if primary else "",
        "attrs": [
            {
                "l1_code": f.l1_domain_code,
                "l1_label": f.l1_label,
                "l2_code": f.l2_code,
                "l2_label": f.l2_label,
                "conf": round(f.confidence, 3),
                "primary": f.is_primary,
            }
            for f in findings
            if f.l1_label  # 只收有歸因者（非問題正向評論 l1 為空，不計入 L1/L2 準確度分母）
        ],
    }


def _run_eval(evalset: list[dict], model: str, workers: int) -> list[dict]:
    """對評測集全量重新初判（ThreadPool + copy_context 繼承 LLM 配置 contextvar）。"""
    ids = [e["rec_oid"] for e in evalset]
    results: list[dict] = []
    failures: list[str] = []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = []
        for start in range(0, len(ids), _FETCH_CHUNK):
            for item in db.get_items_by_ids(ids[start : start + _FETCH_CHUNK], _SOURCE):
                ctx = copy_context()  # 每則獨立快照（同一 Context 不可並發 run）
                futures.append(ex.submit(ctx.run, _judge_one, item, model))
        total = len(futures)
        print(f"開跑：{total} 則 × workers={workers} × model={model}")
        for i, fut in enumerate(as_completed(futures), 1):
            try:
                results.append(fut.result())
            except Exception as exc:  # noqa: BLE001  單則失敗隔離，不中斷整批
                failures.append(str(exc))
                if len(failures) <= 3:  # 只印前 3 則，其餘彙總於收尾
                    print(f"  ⚠️ 單則失敗：{exc}")
            if i % 20 == 0 or i == total:
                print(f"  進度 {i}/{total}（{time.time() - t0:.0f}s）")
    # ⚠️ 失敗率必須顯性化：2026-08-10 踩過——l3_label 欄位在 L1~L4 改名後消失，
    # 100 則有 66 則拋錯被上面的 except 吃掉，卻仍印「✅ 評測完成 34 則」，看起來像成功。
    if failures:
        from collections import Counter
        top = Counter(failures).most_common(3)
        rate = len(failures) / max(1, total)
        print(f"  ❌ 失敗 {len(failures)}/{total}（{rate:.0%}）：{top}")
        if rate > 0.1:
            raise SystemExit(f"失敗率 {rate:.0%} > 10%，拒絕產出不完整結果——先修錯誤再跑。")
    return results


def main() -> None:
    ap = argparse.ArgumentParser(description="多模型準確度評測（唯讀）")
    ap.add_argument("--build-set", action="store_true", help="建評測集（免 token）")
    ap.add_argument("--date-from", default="2026-06-30", help="評測集日期窗起（含）")
    ap.add_argument("--date-to", default="2026-07-09", help="評測集日期窗迄（不含）")
    ap.add_argument("--all-freetag", action="store_true", help="不限已初判（預設只收已初判，對齊現有 Sheet）")
    ap.add_argument("--eval-set", help="評測集 JSON 路徑（run 模式必填）")
    ap.add_argument("--area", default="prejudge", help="功能區默認旋鈕基準（prejudge/prompt_debug/sandbox）")
    ap.add_argument("--provider", default="", help="覆寫供應商連線（切換模型；空＝area 默認）")
    ap.add_argument("--model", default="", help="覆寫 model（空＝area 默認）")
    ap.add_argument("--limit", type=int, default=0, help="pilot：只跑前 N 則")
    ap.add_argument("--workers", type=int, default=0, help="併發數（預設 min(8, env)）")
    ap.add_argument("--out", required=True, help="輸出 JSON 路徑")
    args = ap.parse_args()

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)

    if args.build_set:
        evalset = _build_evalset(args.date_from, args.date_to, judged_only=not args.all_freetag)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(evalset, f, ensure_ascii=False, indent=1)
        print(f"✅ 評測集 {len(evalset)} 則（{args.date_from}~{args.date_to}）→ {args.out}")
        return

    if not args.eval_set:
        ap.error("run 模式需 --eval-set")

    overrides = {"provider": args.provider or None, "model": args.model or None}
    eff = app_settings.effective_llm_dict(
        app_settings.load_settings(), area=args.area, overrides=overrides
    )
    app_settings.set_current(eff)
    if client.is_stub():
        print("❌ stub 模式（該配置無可用 LLM token），拒跑避免產出啟發式假結果。")
        sys.exit(1)

    with open(args.eval_set, encoding="utf-8") as f:
        evalset = json.load(f)
    if args.limit:
        evalset = evalset[: args.limit]

    model = eff.get("model", "")
    provider = eff.get("provider", "")
    workers = args.workers or min(8, env.prejudge_max_workers)
    client.set_llm_cache_read(False)  # 量測 run-to-run 真實行為，禁讀 exact-cache（寫入照常回填）
    usage_buf = client.open_usage_buffer()
    client.set_usage_context({"job_id": f"multi_model_eval_{provider}"})
    try:
        results = _run_eval(evalset, model, workers)
    finally:
        if usage_buf:
            try:
                db.insert_llm_usage_rows(usage_buf)
            except Exception as exc:  # noqa: BLE001  用量落庫失敗不影響評測產出
                print(f"  ⚠️ llm_usage 落庫失敗：{exc}")

    payload = {
        "provider": provider,
        "model": model,
        "n": len(results),
        "results": sorted(results, key=lambda r: r["rec_oid"]),
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print(f"✅ {provider}/{model} 評測完成 {len(results)} 則 → {args.out}")


if __name__ == "__main__":
    main()

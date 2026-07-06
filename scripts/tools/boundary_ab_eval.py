#!/usr/bin/env python3
"""商品內容嚴格界線 A/B 離線評測（唯讀：不寫 judgments、不動線上 cascade 設定）。

背景：content 過度歸因（「說好X實際Y/現場縮水/主觀期待」被誤判進 C-1）。判準已全面
config 化修正後，需量化驗證：① 新規則 + 單次扁平（flat，線上現狀）② 新規則 + cascade
兩階段（domain-first，Stage A 只判域、Stage B 只 offer 選定域 L3）誰的 content 誤判率低。

三種模式：
- --build：從 judgments 抽評測集寫 JSON——content 疑似組（曾被判 content 的負向評論全量）
  + 對照組（其他五域各 N 筆、排除 content 重疊），並附「改前」歸因供對照。
- --mode flat：當前 DB active 規則 + 單次扁平 Stage2 重判評測集（cascade 維持 off）。
- --mode cascade：monkey-patch global_rule.cascade() enabled=True（僅本 process 記憶體，
  不寫 DB、不影響線上），走兩階段；--stage-a-model 可覆寫 Stage A 域分類模型（方案 B）。

- 唯讀：只呼叫 prejudge.to_findings 收集結果，絕不 replace_source_findings。
- 需真 LLM：--user 載入該帳號 DB settings（provider_tokens）；stub（無 token）拒跑。
- llm_usage 照常落庫（job_id=boundary_ab_<mode>），消耗可在 💰AI 消耗頁追蹤。

用法（backend venv）：
    cd backend
    .venv/bin/python ../scripts/tools/boundary_ab_eval.py --build --out ../tmp/boundary_ab/evalset.json
    .venv/bin/python ../scripts/tools/boundary_ab_eval.py --user you@kkday.com \
        --eval-set ../tmp/boundary_ab/evalset.json --mode flat --out ../tmp/boundary_ab/flat.json
    .venv/bin/python ../scripts/tools/boundary_ab_eval.py --user you@kkday.com \
        --eval-set ../tmp/boundary_ab/evalset.json --mode cascade --out ../tmp/boundary_ab/cascade.json
    # 方案 B（Stage A 換強模型）：
    #   ... --mode cascade --stage-a-model gpt-5-mini --out ../tmp/boundary_ab/cascade_mini.json
    # pilot：任一 run 模式加 --limit 6
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

from sqlalchemy import text  # noqa: E402

from app.core import db  # noqa: E402
from app.core import settings as app_settings  # noqa: E402
from app.core.config import env  # noqa: E402
from app.core.db import tables as T  # noqa: E402
from app.judge.llm import client  # noqa: E402

_CONTROL_DOMAINS = ["supplier", "customer", "product_quality", "service", "redemption"]
_FETCH_CHUNK = 200


def _build_evalset(control_per_domain: int) -> list[dict]:
    """抽評測集：content 疑似組全量 + 五域對照組（排除與 content 重疊者），附改前歸因。"""
    with T.get_engine().connect() as c:
        suspect = c.execute(
            text(
                "SELECT DISTINCT source, source_id FROM judgments "
                "WHERE polarity='negative' AND l1_code='content' "
                "ORDER BY source, source_id"
            )
        ).all()
        control: list = []
        for d in _CONTROL_DOMAINS:
            control += c.execute(
                text(
                    "SELECT DISTINCT source, source_id FROM judgments j "
                    "WHERE polarity='negative' AND l1_code=:d AND NOT EXISTS ("
                    "  SELECT 1 FROM judgments x WHERE x.source=j.source"
                    "  AND x.source_id=j.source_id AND x.l1_code='content')"
                    "ORDER BY source, source_id LIMIT :n"
                ),
                {"d": d, "n": control_per_domain},
            ).all()
        keys = [(s, sid, "content_suspect") for s, sid in suspect]
        seen = {(s, sid) for s, sid in suspect}
        for s, sid in control:
            if (s, sid) not in seen:  # 對照組跨域可能重疊，去重
                keys.append((s, sid, "control"))
                seen.add((s, sid))
        # 改前歸因（全部一次撈，記憶體組裝）
        before = c.execute(
            text(
                "SELECT source, source_id, l1_code, l2_code, l3_code, l3_label,"
                " conf_value, is_primary FROM judgments WHERE polarity='negative'"
            )
        ).all()
    before_map: dict = {}
    for s, sid, l1, l2, l3, l3l, conf, prim in before:
        before_map.setdefault((s, sid), []).append(
            {"l1": l1, "l2": l2, "l3": l3, "l3_label": l3l, "conf": conf, "primary": prim}
        )
    return [
        {"source": s, "source_id": sid, "group": g, "before": before_map.get((s, sid), [])}
        for s, sid, g in keys
    ]


def _judge_one(item: dict, source: str, model: str) -> dict:
    """單筆重判（唯讀）：normalize（複製 prejudge_batch._work_one 配方）→ to_findings。"""
    from app.core import source_mapping as _srcmap
    from app.core.db import source_registry as _reg
    from app.judge import prejudge

    spec = _reg.spec_for(source)
    canon = _srcmap.normalize_row(source, item) if source in _srcmap.sources() else {}
    source_id = str(item.get(spec.natural_key) or "") if spec else ""
    client.set_usage_context({"job_id": "boundary_ab", "source": source, "source_id": source_id})
    norm = dict(item)
    norm["source"] = source
    norm["source_id"] = source_id
    norm["content"] = canon.get("content") or ""
    norm["prod_oid"] = canon.get("prod_oid") or ""
    norm["order_oid"] = canon.get("order_oid") or ""
    norm["raw"] = item
    findings = prejudge.to_findings(norm, model=model)
    return {
        "source": source,
        "source_id": source_id,
        "text": norm["content"],
        "attrs": [
            {
                "l1": f.l1_domain_code,
                "l1_label": f.l1_label,
                "l2": f.l2_code,
                "l3": f.l3_code,
                "l3_label": f.l3_label,
                "conf": round(f.confidence, 3),
                "polarity": f.polarity,
                "primary": f.is_primary,
                "tier": f.confidence_tier,
                "summary": (f.summary or {}).get("zh-tw", ""),
                "evidence": f.evidence_quote,
                "model": f.model_used,
            }
            for f in findings
        ],
    }


def _run_eval(evalset: list[dict], model: str, workers: int) -> list[dict]:
    """對評測集全量重判（ThreadPool + copy_context 繼承 LLM 配置 contextvar）。"""
    by_source: dict[str, list[dict]] = {}
    for e in evalset:
        by_source.setdefault(e["source"], []).append(e)
    group_of = {(e["source"], e["source_id"]): e["group"] for e in evalset}
    before_of = {(e["source"], e["source_id"]): e["before"] for e in evalset}

    results: list[dict] = []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = []
        for source, entries in by_source.items():
            ids = [e["source_id"] for e in entries]
            for start in range(0, len(ids), _FETCH_CHUNK):
                for item in db.get_items_by_ids(ids[start : start + _FETCH_CHUNK], source):
                    c = copy_context()  # 每筆獨立快照（同一 Context 不可並發 run）
                    futures.append(ex.submit(c.run, _judge_one, item, source, model))
        total = len(futures)
        print(f"開跑：{total} 筆 × workers={workers} × model={model}")
        for i, fut in enumerate(as_completed(futures), 1):
            try:
                r = fut.result()
                k = (r["source"], r["source_id"])
                r["group"] = group_of.get(k, "")
                r["before"] = before_of.get(k, [])
                results.append(r)
            except Exception as exc:  # noqa: BLE001  單筆失敗隔離，不中斷整批
                print(f"  ⚠️ 單筆失敗：{exc}")
            if i % 10 == 0 or i == total:
                print(f"  進度 {i}/{total}（{time.time() - t0:.0f}s）")
    return results


def main() -> None:
    ap = argparse.ArgumentParser(description="商品內容嚴格界線 A/B 離線評測")
    ap.add_argument("--build", action="store_true", help="抽評測集（免 token）")
    ap.add_argument("--control-per-domain", type=int, default=12)
    ap.add_argument("--eval-set", help="評測集 JSON 路徑（run 模式必填）")
    ap.add_argument("--mode", choices=["flat", "cascade"], help="flat=單次扁平；cascade=兩階段")
    ap.add_argument("--stage-a-model", default="", help="cascade Stage A 域分類模型覆寫（方案 B）")
    ap.add_argument("--user", help="以該帳號 DB settings 啟用真 LLM")
    ap.add_argument("--model", default="", help="主判決模型覆寫（預設用該帳號 active 配置）")
    ap.add_argument("--limit", type=int, default=0, help="pilot：只跑前 N 筆")
    ap.add_argument("--workers", type=int, default=0, help="併發數（預設 min(8, env)）")
    ap.add_argument("--out", required=True, help="輸出 JSON 路徑")
    args = ap.parse_args()

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)

    if args.build:
        evalset = _build_evalset(args.control_per_domain)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(evalset, f, ensure_ascii=False, indent=1)
        n_sus = sum(1 for e in evalset if e["group"] == "content_suspect")
        print(f"✅ 評測集 {len(evalset)} 筆（content 疑似 {n_sus} + 對照 {len(evalset) - n_sus}）→ {args.out}")
        return

    if not (args.eval_set and args.mode and args.user):
        ap.error("run 模式需 --eval-set + --mode + --user")

    u = db.get_user_by_email(args.user)
    if not u:
        print(f"❌ 找不到 user：{args.user}")
        sys.exit(1)
    eff = app_settings.effective_llm_dict(app_settings.load_settings(u["user_id"]))
    app_settings.set_current(eff)
    if client.is_stub():
        print("❌ stub 模式（該帳號無可用 LLM token），拒跑避免產出啟發式假結果。")
        sys.exit(1)

    if args.mode == "cascade":  # 僅本 process 開 cascade（不寫 DB、不影響線上）
        from app.core import global_rule

        _orig_cascade = global_rule.cascade

        def _patched_cascade() -> dict:
            c = dict(_orig_cascade())
            c["enabled"] = True
            if args.stage_a_model:
                sa = dict(c.get("stageA_l1") or {})
                sa["model"] = args.stage_a_model
                c["stageA_l1"] = sa
            return c

        global_rule.cascade = _patched_cascade
        print(f"cascade 已於本 process 啟用（stage_a_model={args.stage_a_model or '(沿用)'}）")

    with open(args.eval_set, encoding="utf-8") as f:
        evalset = json.load(f)
    if args.limit:
        evalset = evalset[: args.limit]

    model = args.model or eff.get("model", "")
    workers = args.workers or min(8, env.prejudge_max_workers)
    usage_buf = client.open_usage_buffer()  # 批次結束一次 bulk insert
    client.set_usage_context({"job_id": f"boundary_ab_{args.mode}"})
    try:
        results = _run_eval(evalset, model, workers)
    finally:
        if usage_buf:
            try:
                db.insert_llm_usage_rows(usage_buf)
            except Exception as exc:  # noqa: BLE001  用量落庫失敗不影響評測產出
                print(f"  ⚠️ llm_usage 落庫失敗：{exc}")

    payload = {
        "mode": args.mode,
        "model": model,
        "stage_a_model": args.stage_a_model,
        "n": len(results),
        "results": sorted(results, key=lambda r: (r["source"], r["source_id"])),
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print(f"✅ {args.mode} 評測完成 {len(results)} 筆 → {args.out}")


if __name__ == "__main__":
    main()

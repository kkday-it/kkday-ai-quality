"""單支 Prompt 評測 harness — 對 7 支評測 prompt（polarity / C-1~C-6）逐支獨立驗證調適效果。

定位：調適閉環的驗證端。改單支模板（config/ai_judge/prompt_templates/）或該域 DB 規則後，
只跑這一支對 N 則已判評論驗證，與 production judgments 比對出該支指標——不跑其他六支、
不動 production 判決管線。prompt 由 gen_eval_prompt_pack 的渲染函式**即時**從 DB active 規則
+ 模板渲染（非讀舊 pack 檔），天然不過期。

指標：
    域 prompt（--prompt C-N）：primary 一致率（production primary 落本域者，本支最高信心 l2 是否同碼）、
        棄權正確率（production 無本域歸因者，本支是否回空）、命中率（production 有本域歸因者本支非空）、
        多報率（本支條數 > production 本域條數）。
    極性 prompt（--prompt polarity）：polarity 一致率 + sentiment 完全一致率。

抽樣：md5(source_id) 排序＝跨 run 穩定（同 N 同樣本，模板 A/B 前後可比）。
LLM token 走 user_settings（--user → effective_llm_dict → set_current；stub 拒跑）；
關 exact-cache 讀取量測真實行為。

用法（scripts/ 未掛載，先 docker cp——比照 gen_eval_prompt_pack.py 慣例）：
    docker cp scripts/tools/{eval_prompt_single.py,gen_eval_prompt_pack.py} \
        kkday-ai-quality-backend:/app/scripts/tools/
    docker exec kkday-ai-quality-backend python /app/scripts/tools/eval_prompt_single.py \
        --prompt C-3 --n 20 --user alvin.bian@kkday.com --out /app/tmp/eval_C-3.json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "backend"))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from sqlalchemy import text  # noqa: E402

from app.core import db  # noqa: E402
from app.core import settings as app_settings  # noqa: E402
from app.core.db import tables as T  # noqa: E402
from app.core.db._shared import read_judgment_config  # noqa: E402
from app.judge.llm import client  # noqa: E402

_SOURCE = "product_reviews"


def _load_generator():
    """載入同目錄的 gen_eval_prompt_pack（渲染函式單一來源，禁止第三份 prompt 組裝邏輯）。"""
    path = Path(__file__).with_name("gen_eval_prompt_pack.py")
    spec = importlib.util.spec_from_file_location("gen_eval_prompt_pack", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _review_texts(ids: list[str]) -> dict[str, str]:
    """rec_oid → 評論文字（title + desc 合併，對齊判決輸入）。"""
    if not ids:
        return {}
    with T.get_engine().connect() as c:
        rows = c.execute(
            text(
                "SELECT rec_oid::text AS sid, coalesce(rec_title,'') AS t, coalesce(rec_desc,'') AS d "
                "FROM product_reviews WHERE rec_oid::text = ANY(:ids)"
            ),
            {"ids": ids},
        ).all()
    return {sid: (f"{t}\n{d}".strip()) for sid, t, d in rows}


def _sample_domain(dom_machine: str, n: int) -> list[dict]:
    """域評測集：production 判過本域（含 primary）與判過他域（棄權分母）各半，md5 穩定排序。

    dom_machine＝judgments.l1_code 存的機器值（supplier/content…），非 C-N。
    """
    half = n // 2
    with T.get_engine().connect() as c:
        pos = c.execute(
            text(
                "SELECT source_id, min(polarity) AS polarity FROM judgments "
                "WHERE source=:s AND l1_code=:d AND polarity IN ('negative','neutral') "
                "GROUP BY source_id ORDER BY md5(source_id) LIMIT :k"
            ),
            {"s": _SOURCE, "d": dom_machine, "k": n - half},
        ).all()
        neg = c.execute(
            text(
                "SELECT j.source_id, min(j.polarity) AS polarity FROM judgments j "
                "WHERE j.source=:s AND j.polarity IN ('negative','neutral') "
                "AND j.l1_code IS NOT NULL AND j.l1_code <> '' AND j.l1_code <> :d "
                "AND NOT EXISTS (SELECT 1 FROM judgments x WHERE x.source=:s "
                "  AND x.source_id=j.source_id AND x.l1_code=:d) "
                "GROUP BY j.source_id ORDER BY md5(j.source_id) LIMIT :k"
            ),
            {"s": _SOURCE, "d": dom_machine, "k": half},
        ).all()
        prod = c.execute(
            text(
                "SELECT source_id, l2_code, coalesce(is_primary,false) AS is_primary "
                "FROM judgments WHERE source=:s AND l1_code=:d "
                "AND source_id = ANY(:ids)"
            ),
            {"s": _SOURCE, "d": dom_machine, "ids": [r[0] for r in pos] + [r[0] for r in neg]},
        ).all()
    by_rec: dict[str, dict] = {}
    for sid, pol in list(pos) + list(neg):
        by_rec[sid] = {"source_id": sid, "polarity": pol, "prod_l2s": [], "prod_primary": None}
    for sid, l2, is_primary in prod:
        by_rec[sid]["prod_l2s"].append(l2)
        if is_primary:
            by_rec[sid]["prod_primary"] = l2
    texts = _review_texts(list(by_rec))
    return [dict(v, text=texts.get(k, "")) for k, v in by_rec.items() if texts.get(k, "").strip()]


def _sample_polarity(n: int) -> list[dict]:
    """極性評測集：三態各 n/3（md5 穩定排序），帶 production polarity/sentiment 真值。"""
    per = max(1, n // 3)
    out: list[dict] = []
    with T.get_engine().connect() as c:
        for pol in ("negative", "neutral", "positive"):
            rows = c.execute(
                text(
                    "SELECT source_id, min(polarity) AS polarity, "
                    "min(sentiment_score) AS sentiment FROM judgments "
                    "WHERE source=:s AND polarity=:p AND sentiment_score IS NOT NULL "
                    "GROUP BY source_id ORDER BY md5(source_id) LIMIT :k"
                ),
                {"s": _SOURCE, "p": pol, "k": per},
            ).all()
            out += [
                {"source_id": sid, "polarity": p, "sentiment": s} for sid, p, s in rows
            ]
    texts = _review_texts([r["source_id"] for r in out])
    return [dict(r, text=texts.get(r["source_id"], "")) for r in out if texts.get(r["source_id"], "").strip()]


def _run_domain(gen, dom_code: str, samples: list[dict]) -> dict:
    """跑單域 prompt 並計指標。"""
    domains = gen._domains()
    dom = next(d for d in domains if d["code"] == dom_code)
    max_n = int(read_judgment_config().get("prejudge", {}).get("max_attributions", 2))
    p = gen._domain_prompt(dom, domains, max_lift=8, max_n=max_n)
    stats = {"primary_total": 0, "primary_match": 0, "abstain_total": 0, "abstain_ok": 0,
             "hit_total": 0, "hit_ok": 0, "over_report": 0}
    mismatches = []
    for i, s in enumerate(samples, 1):
        user = p["user_template"].replace("{POLARITY}", s["polarity"]).replace("{TEXT}", s["text"][:2000])
        resp = client.chat_json(p["system"], user, stage=f"eval_{dom_code}", schema=p["schema"])
        attrs = sorted(resp.get("attributions") or [], key=lambda a: -float(a.get("confidence") or 0))
        pack_l2s = [a.get("l2_code") for a in attrs]
        if s["prod_l2s"]:
            stats["hit_total"] += 1
            if pack_l2s:
                stats["hit_ok"] += 1
        else:
            stats["abstain_total"] += 1
            if not pack_l2s:
                stats["abstain_ok"] += 1
        if s["prod_primary"]:
            stats["primary_total"] += 1
            if pack_l2s and pack_l2s[0] == s["prod_primary"]:
                stats["primary_match"] += 1
        if len(pack_l2s) > len(s["prod_l2s"]):
            stats["over_report"] += 1
        ok = (bool(pack_l2s) == bool(s["prod_l2s"])) and (
            not s["prod_primary"] or (pack_l2s and pack_l2s[0] == s["prod_primary"])
        )
        if not ok:
            mismatches.append({"source_id": s["source_id"], "prod": s["prod_l2s"],
                               "prod_primary": s["prod_primary"], "pack": pack_l2s,
                               "text": s["text"][:120]})
        print(f"  [{i}/{len(samples)}] {s['source_id']} prod={s['prod_l2s'] or '棄權'} pack={pack_l2s or '棄權'}", flush=True)
    n = len(samples)
    return {
        "prompt": dom_code, "n": n,
        "primary_match_rate": round(stats["primary_match"] / stats["primary_total"], 3) if stats["primary_total"] else None,
        "abstain_correct_rate": round(stats["abstain_ok"] / stats["abstain_total"], 3) if stats["abstain_total"] else None,
        "hit_rate": round(stats["hit_ok"] / stats["hit_total"], 3) if stats["hit_total"] else None,
        "over_report_rate": round(stats["over_report"] / n, 3) if n else None,
        "counts": stats, "mismatches": mismatches,
    }


def _run_polarity(gen, samples: list[dict]) -> dict:
    """跑極性 prompt 並計一致率。"""
    p = gen._polarity_prompt()
    pol_ok = sent_ok = 0
    mismatches = []
    for i, s in enumerate(samples, 1):
        user = p["user_template"].replace("{TEXT}", s["text"][:2000])
        resp = client.chat_json(p["system"], user, stage="eval_polarity", schema=p["schema"])
        if resp.get("polarity") == s["polarity"]:
            pol_ok += 1
        else:
            mismatches.append({"source_id": s["source_id"], "prod": s["polarity"],
                               "pack": resp.get("polarity"), "text": s["text"][:120]})
        if resp.get("sentiment") == s["sentiment"]:
            sent_ok += 1
        print(f"  [{i}/{len(samples)}] {s['source_id']} prod={s['polarity']}/{s['sentiment']} "
              f"pack={resp.get('polarity')}/{resp.get('sentiment')}", flush=True)
    n = len(samples)
    return {"prompt": "polarity", "n": n,
            "polarity_match_rate": round(pol_ok / n, 3) if n else None,
            "sentiment_match_rate": round(sent_ok / n, 3) if n else None,
            "mismatches": mismatches}


def main() -> None:
    """CLI：--prompt polarity|C-1..C-6 → 抽樣 → 單支跑 → 指標 JSON。"""
    ap = argparse.ArgumentParser(description="單支 Prompt 評測（polarity / C-1~C-6）")
    ap.add_argument("--prompt", required=True, help="polarity 或 C-1..C-6")
    ap.add_argument("--n", type=int, default=20, help="樣本數（md5 穩定排序，跨 run 可比）")
    ap.add_argument("--user", required=True, help="user_settings token 來源（email）")
    ap.add_argument("--config-id", default="", help="指定 LLM 配置 id（空＝active）")
    ap.add_argument("--out", default="", help="結果 JSON 輸出路徑（空＝只印 stdout）")
    args = ap.parse_args()

    u = db.get_user_by_email(args.user)
    if not u:
        print(f"❌ 找不到 user：{args.user}")
        sys.exit(1)
    eff = app_settings.effective_llm_dict(
        app_settings.load_settings(u["user_id"]), config_id=args.config_id or None
    )
    app_settings.set_current(eff)
    if client.is_stub():
        print("❌ stub 模式（該配置無可用 LLM token），拒跑避免假結果。")
        sys.exit(1)
    client.set_llm_cache_read(False)  # 量測真實行為（寫入照常回填）
    client.set_usage_context({"job_id": f"eval_prompt_{args.prompt}"})

    gen = _load_generator()
    if args.prompt == "polarity":
        samples = _sample_polarity(args.n)
        print(f"樣本 {len(samples)} 則（三態分層）· model={eff.get('model')}", flush=True)
        result = _run_polarity(gen, samples)
    elif args.prompt.startswith("C-"):
        dom = next((d for d in gen._domains() if d["code"] == args.prompt), None)
        if not dom:
            ap.error(f"未知域：{args.prompt}")
            return
        samples = _sample_domain(dom["domain"], args.n)
        print(f"樣本 {len(samples)} 則（本域/他域各半）· model={eff.get('model')}", flush=True)
        result = _run_domain(gen, args.prompt, samples)
    else:
        ap.error("--prompt 須為 polarity 或 C-1..C-6")
        return
    result["model"] = eff.get("model")
    print(json.dumps({k: v for k, v in result.items() if k != "mismatches"}, ensure_ascii=False))
    print(f"分歧 {len(result['mismatches'])} 則" + ("（詳見 --out）" if args.out else ""))
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"→ {args.out}")


if __name__ == "__main__":
    main()

"""單支 Prompt 評測 harness — 對 7 支判決 prompt（polarity / C-1~C-6）逐支獨立驗證調適效果。

定位：Prompt-as-Source 調適閉環的驗證端。改單支 prompt（docs/prompts/*.md 或線上熱編 DB
active 版）後，只跑這一支對 N 則參照集驗證，出該支指標——不跑其他六支、不動 production 判決管線。
prompt 直接讀 **prompt_source**（判決引擎同源 SSOT：DB active→檔案 fallback），天然與線上一致。

參照集：judgments 現行判決為參照（本域正/他域負各半），量測「與現行判決一致度」。

指標（純函式，SSOT＝後端 app.judge.prompt_eval.compute_*_metrics，UI「測試」端點共用）：
    域 prompt：primary 一致率 / 棄權正確率 / 命中率 / 多報率。
    極性 prompt：polarity 一致率 + sentiment 一致率（參照恆用 production polarity）。

A/B（--compare baseline.json）：對上一輪結果逐案 diff（improvements / regressions），調適前後可比。
一致性（--repeats N）：同樣本重跑 N 次，報 primary 抖動（prompt 穩定度）。
抽樣 md5(id) 排序＝跨 run 穩定。LLM token 走 user_settings（--user；stub 拒跑），關 exact-cache 量測真實。

用法（scripts/ 未掛載，先 docker cp）：
    docker cp scripts/tools/eval_prompt_single.py kkday-ai-quality-backend:/app/scripts/tools/
    docker exec kkday-ai-quality-backend python /app/scripts/tools/eval_prompt_single.py \
        --prompt C-3 --n 60 \
        --user alvin.bian@kkday.com --compare /app/tmp/BASELINE_C-3.json --out /app/tmp/eval_C-3.json
"""

from __future__ import annotations

import argparse
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
from app.judge import prompt_source  # noqa: E402
from app.judge.llm import client  # noqa: E402
from app.judge.prompt_eval import (  # noqa: E402  指標純函式 SSOT（後端，UI 端點共用）
    compute_domain_metrics,
    compute_polarity_metrics,
)

_SOURCE = "product_reviews"


# ─────────────────────────── prompt 對照 ───────────────────────────
def _prompt_id_of(arg: str) -> str:
    """--prompt 值（polarity / C-1..C-6）→ prompt_source 的 prompt_id。"""
    if arg == "polarity":
        return prompt_source.POLARITY_ID
    pid = prompt_source.prompt_id_for_rule(f"prompt_{arg}")  # C-3 → prompt_C-3 → 03_C-3_supplier
    if not pid:
        raise SystemExit(f"未知 prompt：{arg}（須為 polarity 或 C-1..C-6）")
    return pid


def _domain_of_prompt(arg: str) -> str:
    """--prompt C-N → 對應歸因分類域機器值（自 prompt_source 檔名尾綴派生，如 C-3→supplier）。"""
    return prompt_source._domain_of(_prompt_id_of(arg))


# ─────────────────────────── 取文字 ───────────────────────────
def _review_texts(ids: list[str]) -> dict[str, str]:
    """rec_oid → 評論文字（title + desc，對齊判決輸入）。"""
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


# ─────────────────────────── 抽樣 ───────────────────────────
def _sample_domain(dom_machine: str, n: int) -> list[dict]:
    """域參照：judgments 判過本域（含 primary）與判過他域（棄權分母）各半，md5 穩定排序。

    回統一參照記錄：{id, text, polarity, ref_l2s, ref_primary}。
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
                "FROM judgments WHERE source=:s AND l1_code=:d AND source_id = ANY(:ids)"
            ),
            {"s": _SOURCE, "d": dom_machine, "ids": [r[0] for r in pos] + [r[0] for r in neg]},
        ).all()
    by_rec: dict[str, dict] = {}
    for sid, pol in list(pos) + list(neg):
        by_rec[sid] = {"id": sid, "polarity": pol, "ref_l2s": [], "ref_primary": None}
    for sid, l2, is_primary in prod:
        by_rec[sid]["ref_l2s"].append(l2)
        if is_primary:
            by_rec[sid]["ref_primary"] = l2
    texts = _review_texts(list(by_rec))
    return [dict(v, text=texts.get(k, "")) for k, v in by_rec.items() if texts.get(k, "").strip()]


def _sample_polarity(n: int) -> list[dict]:
    """極性參照集：三態各 n/3（md5 穩定），帶 production polarity/sentiment 真值。"""
    per = max(1, n // 3)
    out: list[dict] = []
    with T.get_engine().connect() as c:
        for pol in ("negative", "neutral", "positive"):
            rows = c.execute(
                text(
                    "SELECT source_id, min(polarity) AS polarity, min(sentiment_score) AS sentiment "
                    "FROM judgments WHERE source=:s AND polarity=:p AND sentiment_score IS NOT NULL "
                    "GROUP BY source_id ORDER BY md5(source_id) LIMIT :k"
                ),
                {"s": _SOURCE, "p": pol, "k": per},
            ).all()
            out += [{"id": sid, "polarity": p, "sentiment": s} for sid, p, s in rows]
    texts = _review_texts([r["id"] for r in out])
    return [dict(r, text=texts.get(r["id"], "")) for r in out if texts.get(r["id"], "").strip()]


# 指標純函式（compute_domain_metrics / compute_polarity_metrics）為 SSOT，已收斂至後端
# app.judge.prompt_eval（UI「測試」端點與本 CLI 共用，避免判準邏輯平行兩份）；此處直接 import。


# ─────────────────────────── 跑單支（I/O：呼叫 LLM 產 records → 純函式算指標）───────────────────────────
def _run_domain(pid: str, dom_code: str, samples: list[dict], repeats: int) -> dict:
    """跑單域 prompt（repeats 次取穩定度）→ records → 指標。"""
    p = prompt_source.load(pid)
    records: list[dict] = []
    jitter = 0  # primary 抖動計數（repeats>1 時，同筆多次 primary 不一致）
    for i, s in enumerate(samples, 1):
        user = (
            p["user_template"]
            .replace("{POLARITY}", s["polarity"])
            .replace("{TEXT}", s["text"][:2000])
        )
        primaries: list[str] = []
        last_l2s: list[str] = []
        for _ in range(max(1, repeats)):
            resp = client.chat_json(p["system"], user, stage=f"eval_{dom_code}", schema=p["schema"])
            attrs = sorted(
                resp.get("attributions") or [], key=lambda a: -float(a.get("confidence") or 0)
            )
            last_l2s = [a.get("l2_code") for a in attrs]
            primaries.append(last_l2s[0] if last_l2s else "")
        if len(set(primaries)) > 1:
            jitter += 1
        records.append(
            {
                "id": s["id"],
                "ref_l2s": s["ref_l2s"],
                "ref_primary": s["ref_primary"],
                "pack_l2s": last_l2s,
                "text": s["text"][:120],
            }
        )
        print(
            f"  [{i}/{len(samples)}] {s['id']} ref={s['ref_l2s'] or '棄權'} pack={last_l2s or '棄權'}",
            flush=True,
        )
    m = compute_domain_metrics(records)
    mismatches = [
        {
            "id": r["id"],
            "ref": r["ref_l2s"],
            "ref_primary": r["ref_primary"],
            "pack": r["pack_l2s"],
            "text": r["text"],
        }
        for r in records
        if (bool(r["pack_l2s"]) != bool(r["ref_l2s"]))
        or (r["ref_primary"] and not (r["pack_l2s"] and r["pack_l2s"][0] == r["ref_primary"]))
    ]
    out = {"prompt": dom_code, **m, "mismatches": mismatches, "records": records}
    if repeats > 1:
        out["primary_jitter_rate"] = round(jitter / len(samples), 3) if samples else None
    return out


def _run_polarity(pid: str, samples: list[dict]) -> dict:
    """跑極性 prompt → records → 指標。"""
    p = prompt_source.load(pid)
    records: list[dict] = []
    for i, s in enumerate(samples, 1):
        user = p["user_template"].replace("{TEXT}", s["text"][:2000])
        resp = client.chat_json(p["system"], user, stage="eval_polarity", schema=p["schema"])
        records.append(
            {
                "id": s["id"],
                "polarity": s["polarity"],
                "sentiment": s["sentiment"],
                "pack_polarity": resp.get("polarity"),
                "pack_sentiment": resp.get("sentiment"),
                "text": s["text"][:120],
            }
        )
        print(
            f"  [{i}/{len(samples)}] {s['id']} ref={s['polarity']}/{s['sentiment']} "
            f"pack={resp.get('polarity')}/{resp.get('sentiment')}",
            flush=True,
        )
    m = compute_polarity_metrics(records)
    mismatches = [
        {"id": r["id"], "ref": r["polarity"], "pack": r["pack_polarity"], "text": r["text"]}
        for r in records
        if r["pack_polarity"] != r["polarity"]
    ]
    return {"prompt": "polarity", **m, "mismatches": mismatches, "records": records}


# ─────────────────────────── A/B 比較 ───────────────────────────
def _compare(result: dict, baseline_path: str) -> dict:
    """對 baseline.json 逐案 diff：本輪對而基線錯＝improvement、反之＝regression（以 primary 一致為準）。"""
    base = json.loads(Path(baseline_path).read_text(encoding="utf-8"))
    base_rec = {r["id"]: r for r in base.get("records", [])}

    def _ok(r: dict) -> bool:
        if "pack_polarity" in r:
            return r["pack_polarity"] == r["polarity"]
        return (bool(r["pack_l2s"]) == bool(r["ref_l2s"])) and (
            not r["ref_primary"] or (r["pack_l2s"] and r["pack_l2s"][0] == r["ref_primary"])
        )

    improvements, regressions = [], []
    for r in result.get("records", []):
        b = base_rec.get(r["id"])
        if not b:
            continue
        now, was = _ok(r), _ok(b)
        if now and not was:
            improvements.append(r["id"])
        elif was and not now:
            regressions.append(r["id"])
    return {"improvements": improvements, "regressions": regressions}


def main() -> None:
    """CLI：--prompt polarity|C-1..C-6 → 抽樣 → 單支跑 → 指標 JSON（可 --compare / --repeats）。"""
    ap = argparse.ArgumentParser(description="單支 Prompt 評測（Prompt-as-Source 調適閉環驗證端）")
    ap.add_argument("--prompt", required=True, help="polarity 或 C-1..C-6")
    ap.add_argument("--n", type=int, default=20, help="樣本數（md5 穩定排序，跨 run 可比）")
    ap.add_argument("--user", required=True, help="user_settings token 來源（email）")
    ap.add_argument("--config-id", default="", help="指定 LLM 配置 id（空＝active）")
    ap.add_argument(
        "--compare", default="", help="baseline.json 路徑（逐案 diff improvements/regressions）"
    )
    ap.add_argument("--repeats", type=int, default=1, help="同樣本重跑次數（>1 報 primary 抖動）")
    ap.add_argument("--out", default="", help="結果 JSON 輸出路徑（空＝只印 stdout）")
    args = ap.parse_args()

    u = db.get_user_by_email(args.user)
    if not u:
        raise SystemExit(f"❌ 找不到 user：{args.user}")
    eff = app_settings.effective_llm_dict(
        app_settings.load_settings(u["user_id"]), config_id=args.config_id or None
    )
    app_settings.set_current(eff)
    if client.is_stub():
        raise SystemExit("❌ stub 模式（該配置無可用 LLM token），拒跑避免假結果。")
    client.set_llm_cache_read(False)  # 量測真實行為（寫入照常回填）
    client.set_usage_context({"job_id": f"eval_prompt_{args.prompt}"})

    pid = _prompt_id_of(args.prompt)
    if args.prompt == "polarity":
        samples = _sample_polarity(args.n)
        print(
            f"樣本 {len(samples)} 則（三態分層）· model={eff.get('model')}",
            flush=True,
        )
        result = _run_polarity(pid, samples)
    else:
        dom = _domain_of_prompt(args.prompt)
        if not dom:
            raise SystemExit(f"未知域：{args.prompt}")
        samples = _sample_domain(dom, args.n)
        print(
            f"樣本 {len(samples)} 則（本域/他域各半）· model={eff.get('model')}",
            flush=True,
        )
        result = _run_domain(pid, args.prompt, samples, args.repeats)
    result["model"] = eff.get("model")
    result["source"] = "production"

    if args.compare:
        result["compare"] = _compare(result, args.compare)

    summary = {k: v for k, v in result.items() if k not in ("mismatches", "records")}
    print(json.dumps(summary, ensure_ascii=False))
    print(f"分歧 {len(result['mismatches'])} 則" + ("（詳見 --out）" if args.out else ""))
    if args.compare:
        cmp = result["compare"]
        print(f"vs baseline：改善 {len(cmp['improvements'])} · 倒退 {len(cmp['regressions'])}")
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(
            json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        print(f"→ {args.out}")


if __name__ == "__main__":
    main()

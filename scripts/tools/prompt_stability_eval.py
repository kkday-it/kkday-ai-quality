#!/usr/bin/env python3
"""V1 Prompt 穩定性驗證——對 mock 測試集重複判決 N 次，量測 P/R/F1 與 run-to-run 一致性。

用法（backend venv）：
    cd backend
    # 1) 純管線驗證（免 LLM、免 --user，以 gold 加確定性隨機翻轉模擬預測）：
    .venv/bin/python ../scripts/tools/prompt_stability_eval.py \
        --testset ../tmp/mock_testset/testset_v1.jsonl --dry-run --out ../tmp/prompt_stability

    # 2) 真實評測（會呼叫 LLM、產生 token 費用；沿用 multi_model_eval.py 同一唯讀配方）：
    .venv/bin/python ../scripts/tools/prompt_stability_eval.py \
        --testset ../tmp/mock_testset/testset_v1.jsonl --user you@kkday.com \
        --config-id <字節/Gemini/GPT config id> --repeats 3 --out ../tmp/prompt_stability [--limit 30]

輸出：--out 目錄下 stability_report.json（完整指標＋逐樣本明細）與 stability_report.md（人讀摘要）。

指標涵蓋：
- per-L1 precision/recall/F1 + support（以 N 次重複的多數決結果 vs gold 計）
- overall accuracy、macro-F1、棄權率（判官給 non_issue/none）
- 重複一致性：每樣本 N 次結果完全一致比率＋簡化 pairwise agreement（非嚴謹 Fleiss kappa）
- variant_type 分組準確率（模糊/跨類干擾/異常輸入等各自表現）
- 誤判混淆對 Top10（gold→pred 計數）
- 格式異常率（to_findings 拋錯比率）

P/R/F1 手算（未依賴 sklearn）：已核實 backend venv 含 scikit-learn>=1.3，惟本評測僅 6 類單標籤
分類，手算公式與 sklearn 一致且更輕量（零額外 import、--dry-run 免揹 numpy 啟動成本），故不引入。

唯讀配方與 multi_model_eval.py 同源：ThreadPool + copy_context 繼承 LLM 配置 contextvar、
cache_read=False（量測 run-to-run 真實行為，非讀 exact-cache）、stub 模式拒跑避免產出假結果。
backend app 相關 import 一律延後至實際呼叫 LLM 時才載入（--dry-run / --help 不需要 backend 依賴）。
"""

from __future__ import annotations

import argparse
import itertools
import json
import random
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import copy_context
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BACKEND_DIR = _REPO_ROOT / "backend"
_DEFAULT_OUT_DIR = _REPO_ROOT / "tmp" / "prompt_stability"

# --dry-run 模擬：多數重複判決「答對」的機率（其餘機率均勻翻轉至他域或 none，驗證指標計算管線）
_DRY_RUN_CORRECT_RATE = 0.7


# ── 測試集載入 ────────────────────────────────────────────────────────────


def _load_testset(path: str) -> list[dict]:
    """讀 mock_testset_gen.py 產出的 JSONL 測試集。"""
    samples = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                samples.append(json.loads(line))
    return samples


# ── 真實評測（呼叫 LLM，延後 import backend）────────────────────────────────


def _prepare_real(user_email: str, config_id: str, workers: int) -> tuple[str, str, int]:
    """（真實模式限定）以帳號 DB settings 啟用真 LLM，回傳 (provider, model, workers)。

    backend app 相關 import 集中於此函式內（非模組頂層），確保 --dry-run / --help 不需要
    backend venv 以外的任何 DB / 設定即可執行（重庫 lazy import，見 python.md 規則）。
    """
    if str(_BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(_BACKEND_DIR))
    from app.core import settings as app_settings
    from app.core import db
    from app.core.config import env
    from app.judge.llm import client

    u = db.get_user_by_email(user_email)
    if not u:
        print(f"❌ 找不到 user：{user_email}")
        sys.exit(1)
    eff = app_settings.effective_llm_dict(
        app_settings.load_settings(u["user_id"]), config_id=config_id or None
    )
    app_settings.set_current(eff)
    if client.is_stub():
        print("❌ stub 模式（該配置無可用 LLM token），拒跑避免產出啟發式假結果。")
        sys.exit(1)
    client.set_llm_cache_read(False)  # 量測 run-to-run 真實行為，禁讀 exact-cache
    return eff.get("provider", ""), eff.get("model", ""), workers or min(8, env.prejudge_max_workers)


def _judge_one_real(sample: dict, model: str, repeat_idx: int) -> dict:
    """單樣本單次重判（唯讀）：組 synthetic norm item → to_findings → 取主歸因 L1。

    norm 欄位比照 prejudge._text_of / _base_kwargs 最小需求（content/source/source_id/
    prod_oid/order_oid/raw）；刻意不帶 rating 欄，避免誤觸 Stage0 零 LLM 略過。
    """
    from app.judge import prejudge
    from app.judge.llm import client

    source_id = f"{sample['id']}__r{repeat_idx}"
    client.set_usage_context(
        {"job_id": "prompt_stability_eval", "source": "mock_testset", "source_id": source_id}
    )
    norm = {
        "content": sample["text"],
        "source": "mock_testset",
        "source_id": source_id,
        "prod_oid": "",
        "order_oid": "",
        "raw": {"seed_node": sample.get("seed_node", "")},
    }
    try:
        findings = prejudge.to_findings(norm, model=model)
    except Exception as exc:  # noqa: BLE001  單次失敗獨立標記，不中斷整批
        return {"sample_id": sample["id"], "repeat": repeat_idx, "pred": "none", "error": str(exc)}
    if not findings:
        return {"sample_id": sample["id"], "repeat": repeat_idx, "pred": "none", "error": ""}
    primary = next((f for f in findings if f.is_primary), findings[0])
    return {
        "sample_id": sample["id"],
        "repeat": repeat_idx,
        "pred": primary.l1_domain_code or "none",
        "error": "",
    }


def _run_real(samples: list[dict], model: str, repeats: int, workers: int) -> list[dict]:
    """對測試集全量重判 N 次（ThreadPool + copy_context 繼承 LLM 配置 contextvar）。"""
    from app.core import db
    from app.judge.llm import client

    results: list[dict] = []
    usage_buf = client.open_usage_buffer()
    client.set_usage_context({"job_id": "prompt_stability_eval"})
    t0 = time.time()
    try:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = []
            for s in samples:
                for r in range(repeats):
                    ctx = copy_context()  # 每次獨立快照（同一 Context 不可並發 run）
                    futures.append(ex.submit(ctx.run, _judge_one_real, s, model, r))
            total = len(futures)
            print(f"開跑：{len(samples)} 則 × {repeats} 次重複 × workers={workers}（共 {total} 次判決）")
            for i, fut in enumerate(as_completed(futures), 1):
                try:
                    results.append(fut.result())
                except Exception as exc:  # noqa: BLE001  單次失敗隔離，不中斷整批
                    print(f"  ⚠️ 單次失敗：{exc}")
                if i % 50 == 0 or i == total:
                    print(f"  進度 {i}/{total}（{time.time() - t0:.0f}s）")
    finally:
        if usage_buf:
            try:
                db.insert_llm_usage_rows(usage_buf)
            except Exception as exc:  # noqa: BLE001  用量落庫失敗不影響評測產出
                print(f"  ⚠️ llm_usage 落庫失敗：{exc}")
    return results


# ── --dry-run 管線驗證（免 LLM）──────────────────────────────────────────


def _run_dry(samples: list[dict], repeats: int, domains: list[str], rng: random.Random) -> list[dict]:
    """--dry-run：以 gold 依 _DRY_RUN_CORRECT_RATE 機率原樣輸出，否則確定性隨機翻轉為他域或 none。

    純用於驗證指標計算與報告輸出管線是否正確，不呼叫任何 LLM、不需 --user。
    """
    results = []
    for s in samples:
        for r in range(repeats):
            if rng.random() < _DRY_RUN_CORRECT_RATE:
                pred = s["gold_l1"]
            else:
                pool = [d for d in domains if d != s["gold_l1"]] + ["none"]
                pred = rng.choice(pool)
            results.append({"sample_id": s["id"], "repeat": r, "pred": pred, "error": ""})
    return results


# ── 指標計算（手算，不依賴 sklearn；理由見檔頭 docstring）──────────────────


def _majority_vote(preds: list[str]) -> str:
    """多次重複判決 → 多數決；票數並列時取字典序最小者（確定性 tie-break）。"""
    counts = Counter(preds)
    top_n = counts.most_common(1)[0][1]
    candidates = sorted(label for label, n in counts.items() if n == top_n)
    return candidates[0]


def _compute_prf(pairs: list[tuple[str, str]], classes: list[str]) -> dict:
    """對 L1 域手算 precision/recall/F1 + support（單標籤多類；pred='none' 視為棄權，計入該
    gold 類別的 FN，但不佔任何類別的 FP，避免棄權被誤記為某類的誤判）。

    Args:
        pairs: [(gold, pred), ...]，pred 可能為 'none'（判官未給出歸因/棄權）。
        classes: 六域 machine 值固定順序。

    Returns:
        {"per_class": {class: {precision, recall, f1, support}}, "overall": {...}}。
    """
    tp = dict.fromkeys(classes, 0)
    fp = dict.fromkeys(classes, 0)
    fn = dict.fromkeys(classes, 0)
    support = dict.fromkeys(classes, 0)
    correct = 0
    abstain = 0
    for gold, pred in pairs:
        support[gold] = support.get(gold, 0) + 1
        if pred == "none":
            abstain += 1
            fn[gold] = fn.get(gold, 0) + 1
            continue
        if pred == gold:
            tp[gold] += 1
            correct += 1
        else:
            fn[gold] = fn.get(gold, 0) + 1
            if pred in fp:
                fp[pred] += 1

    per_class = {}
    f1s = []
    for c in classes:
        p = tp[c] / (tp[c] + fp[c]) if (tp[c] + fp[c]) else 0.0
        r = tp[c] / (tp[c] + fn[c]) if (tp[c] + fn[c]) else 0.0
        f1 = 2 * p * r / (p + r) if (p + r) else 0.0
        per_class[c] = {
            "precision": round(p, 4),
            "recall": round(r, 4),
            "f1": round(f1, 4),
            "support": support[c],
        }
        f1s.append(f1)

    total = len(pairs)
    overall = {
        "accuracy": round(correct / total, 4) if total else 0.0,
        "macro_f1": round(sum(f1s) / len(f1s), 4) if f1s else 0.0,
        "abstain_rate": round(abstain / total, 4) if total else 0.0,
        "n": total,
    }
    return {"per_class": per_class, "overall": overall}


def _consistency_stats(sample_preds: dict[str, list[str]]) -> dict:
    """重複一致性：每樣本 N 次結果完全一致比率 ＋ 簡化 pairwise agreement。

    pairwise agreement：每樣本內 C(N,2) 兩兩比較是否同標籤取比例，再對全體樣本平均——可視為
    Fleiss' kappa 的簡化版（未做機會一致性校正，僅原始一致率，足供「穩不穩」的相對比較）。
    """
    full_agree = 0
    pairwise_scores = []
    for preds in sample_preds.values():
        if len(preds) < 2:
            continue
        if len(set(preds)) == 1:
            full_agree += 1
        combos = list(itertools.combinations(preds, 2))
        pairwise_scores.append(sum(1 for a, b in combos if a == b) / len(combos))
    n = len(sample_preds)
    return {
        "full_agreement_rate": round(full_agree / n, 4) if n else 0.0,
        "pairwise_agreement_mean": round(sum(pairwise_scores) / len(pairwise_scores), 4)
        if pairwise_scores
        else 0.0,
        "n_samples": n,
    }


def _variant_accuracy(rows: list[dict]) -> dict:
    """依 variant_type 分組計算多數決準確率（模糊/跨類干擾/異常輸入各自表現）。"""
    groups: dict[str, list[bool]] = {}
    for r in rows:
        groups.setdefault(r["variant_type"], []).append(r["pred_majority"] == r["gold_l1"])
    return {
        vt: {"accuracy": round(sum(flags) / len(flags), 4), "n": len(flags)}
        for vt, flags in sorted(groups.items())
    }


def _confusion_pairs(rows: list[dict], top_n: int = 10) -> list[dict]:
    """誤判混淆對 Top N（gold→pred 計數，僅計不一致者）。"""
    c = Counter((r["gold_l1"], r["pred_majority"]) for r in rows if r["pred_majority"] != r["gold_l1"])
    return [{"gold": g, "pred": p, "count": n} for (g, p), n in c.most_common(top_n)]


def _assemble(samples: list[dict], run_results: list[dict], domains: list[str]) -> dict:
    """彙整逐次判決結果為完整報告（多數決 → P/R/F1 ／一致性 ／型態準確率 ／混淆對 ／異常率）。"""
    by_sample: dict[str, list[str]] = {}
    errors = 0
    empties = 0
    for r in run_results:
        by_sample.setdefault(r["sample_id"], []).append(r["pred"])
        if r["error"]:
            errors += 1
        if r["pred"] == "none":
            empties += 1
    total_runs = len(run_results)

    rows = []
    for s in samples:
        preds = by_sample.get(s["id"], [])
        majority = _majority_vote(preds) if preds else "none"
        rows.append({**s, "preds": preds, "pred_majority": majority})

    pairs = [(r["gold_l1"], r["pred_majority"]) for r in rows]
    return {
        "prf": _compute_prf(pairs, domains),
        "consistency": _consistency_stats({r["id"]: r["preds"] for r in rows}),
        "variant_accuracy": _variant_accuracy(rows),
        "confusion_top10": _confusion_pairs(rows),
        "format_anomaly_rate": round(errors / total_runs, 4) if total_runs else 0.0,
        "abstain_rate_raw": round(empties / total_runs, 4) if total_runs else 0.0,
        "n_samples": len(rows),
        "n_runs_total": total_runs,
        "rows": rows,
    }


# ── 報告輸出 ──────────────────────────────────────────────────────────────


def _render_markdown(report: dict, meta: dict) -> str:
    """把彙整後的報告 dict 轉為人讀 Markdown 摘要。"""
    lines = [
        "# Prompt 穩定性驗證報告",
        "",
        f"- 測試集：`{meta['testset']}`（{report['n_samples']} 筆 × 重複 {meta['repeats']} 次"
        f"＝{report['n_runs_total']} 次判決）",
        f"- 模型：{meta['provider']}/{meta['model']}"
        + ("（--dry-run 模擬，非真實 LLM 判決）" if meta["dry_run"] else ""),
        f"- 產生時間：{meta['generated_at']}",
        "",
        "## 整體指標",
        "",
        f"- Accuracy：{report['prf']['overall']['accuracy']}",
        f"- Macro-F1：{report['prf']['overall']['macro_f1']}",
        f"- 棄權率（多數決為 none）：{report['prf']['overall']['abstain_rate']}",
        f"- 格式異常率（to_findings 拋錯）：{report['format_anomaly_rate']}",
        "",
        "## 重複一致性",
        "",
        f"- 完全一致比率：{report['consistency']['full_agreement_rate']}",
        f"- Pairwise agreement 均值：{report['consistency']['pairwise_agreement_mean']}",
        "",
        "## 各 L1 域 P/R/F1",
        "",
        "| 域 | Precision | Recall | F1 | Support |",
        "|---|---|---|---|---|",
    ]
    for c, m in report["prf"]["per_class"].items():
        lines.append(f"| {c} | {m['precision']} | {m['recall']} | {m['f1']} | {m['support']} |")

    lines += ["", "## variant_type 分組準確率", "", "| variant_type | Accuracy | N |", "|---|---|---|"]
    for vt, m in report["variant_accuracy"].items():
        lines.append(f"| {vt} | {m['accuracy']} | {m['n']} |")

    lines += ["", "## 誤判混淆對 Top10（gold → pred）", "", "| gold | pred | count |", "|---|---|---|"]
    for c in report["confusion_top10"]:
        lines.append(f"| {c['gold']} | {c['pred']} | {c['count']} |")

    return "\n".join(lines) + "\n"


# ── CLI ──────────────────────────────────────────────────────────────────


def main() -> None:
    ap = argparse.ArgumentParser(description="Prompt 穩定性重複一致性評測（P/R/F1＋重複一致性）")
    ap.add_argument("--testset", required=True, help="mock_testset_gen.py 產出的 JSONL 測試集路徑")
    ap.add_argument("--user", help="以該帳號 DB settings 啟用真 LLM（--dry-run 時可省略）")
    ap.add_argument("--config-id", default="", help="指定 LLM 配置 id（切換模型；空＝該帳號 active）")
    ap.add_argument("--repeats", type=int, default=3, help="每則樣本重複判決次數（量測 run-to-run 一致性）")
    ap.add_argument("--limit", type=int, default=0, help="抽樣上限（0＝全跑）")
    ap.add_argument("--workers", type=int, default=0, help="併發數（預設 min(8, env)；--dry-run 不使用）")
    ap.add_argument("--out", default=str(_DEFAULT_OUT_DIR), help="輸出目錄（JSON + Markdown 報告）")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="不呼叫 LLM，以 gold 加確定性隨機翻轉模擬預測，僅驗證指標計算與報告輸出管線",
    )
    ap.add_argument("--seed", type=int, default=42, help="--dry-run 模擬用的確定性亂數種子")
    args = ap.parse_args()

    samples = _load_testset(args.testset)
    if args.limit:
        samples = samples[: args.limit]
    if not samples:
        print("❌ 測試集為空")
        sys.exit(1)
    domains = sorted({s["gold_l1"] for s in samples})

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.dry_run:
        rng = random.Random(args.seed)
        run_results = _run_dry(samples, args.repeats, domains, rng)
        provider, model = "dry-run", "gold+flip-simulation"
    else:
        if not args.user:
            ap.error("非 --dry-run 模式需 --user")
        provider, model, workers = _prepare_real(args.user, args.config_id, args.workers)
        run_results = _run_real(samples, model, args.repeats, workers)

    report = _assemble(samples, run_results, domains)
    meta = {
        "testset": args.testset,
        "provider": provider,
        "model": model,
        "repeats": args.repeats,
        "dry_run": args.dry_run,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    json_path = out_dir / "stability_report.json"
    md_path = out_dir / "stability_report.md"
    with json_path.open("w", encoding="utf-8") as f:
        json.dump({"meta": meta, **report}, f, ensure_ascii=False, indent=1)
    with md_path.open("w", encoding="utf-8") as f:
        f.write(_render_markdown(report, meta))

    print(f"✅ 完成 {report['n_samples']} 筆 × {args.repeats} 次 → {json_path}｜{md_path}")
    print(
        f"Accuracy={report['prf']['overall']['accuracy']} "
        f"Macro-F1={report['prf']['overall']['macro_f1']} "
        f"完全一致率={report['consistency']['full_agreement_rate']} "
        f"格式異常率={report['format_anomaly_rate']}"
    )


if __name__ == "__main__":
    main()

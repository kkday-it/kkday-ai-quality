"""把多模型評測結果（tmp/multi_model/*_v4.json）灌進 attribution_history（kind='prejudge'）。

用途：三模型（ByteDance/Gemini/Claude）初判原本只活在評測 JSON / GSheet，不在 DB。本腳本把它們
以「每評論每模型一筆初判快照」寫入 attribution_history，讓歸因列表的歸因歷史 modal 看得到各模型、
導出可複選並排對比——**完全不碰 attributions 活表**（列表資料源恆為 gpt canonical）。

設計要點：
- 只寫 attribution_history（append-only），不呼叫 replace_source_findings → 不覆蓋 gpt 活表。
- 快照形狀對齊 attribution_history.snapshot_of（l1/l2/l3/confidence/content/is_primary + 頂層
  polarity/sentiment_score），故既有 latest_snapshots / _adapt_snapshot / 前端 modal 直接吃。
- 去重天然：insert_prejudge_event 以 model+params+digest 比對，重跑同 params 自動 skip。
- label 補全：BD/Gemini 自帶 l1_label/l2_label；Claude 只有 C-code → 由 BD/Gemini 聯集 map +
  ai_judge fallback 補；l1 域機器值/中文域名由 C-code 反查（_L1_CODE_TO_DOMAIN + domain_label）。

用法（容器內）：
    docker exec kkday-ai-quality-backend python /app/scripts/tools/persist_multimodel_history.py \
        --dir /app/tmp/multi_model --triggered-by alvin.bian@kkday.com
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# C-code L1 → 域機器值（attributions.l1_code 存機器值，非 C-code）；與 prejudge 六域一致。
_L1_CODE_TO_DOMAIN = {
    "C-1": "content",
    "C-2": "product_quality",
    "C-3": "supplier",
    "C-4": "redemption",
    "C-5": "service",
    "C-6": "customer",
}


def _build_label_maps(files: list[Path]) -> tuple[dict[str, str], dict[str, str]]:
    """從自帶 label 的模型檔（BD/Gemini）聯集出 {l1_code(域機器值)→中文域名} 與 {l2_code(C)→葉 label}。

    Claude 檔只有 C-code、無 label，靠此聯集 + ai_judge fallback 補齊。
    """
    l1_lab: dict[str, str] = {}
    l2_lab: dict[str, str] = {}
    for f in files:
        for r in json.loads(f.read_text(encoding="utf-8")).get("results", []):
            for a in r.get("attrs", []):
                if a.get("l1_code") and a.get("l1_label"):
                    l1_lab[a["l1_code"]] = a["l1_label"]
                if a.get("l2_code") and a.get("l2_label"):
                    l2_lab[a["l2_code"]] = a["l2_label"]
    return l1_lab, l2_lab


def _norm_attr(a: dict, l1_lab: dict[str, str], l2_lab: dict[str, str]) -> tuple[str, str, str, str]:
    """單筆 attr（BD/Gemini 帶 label 或 Claude 只帶 C-code）→ (l1_domain, l1_label, l2_code, l2_label)。

    統一以 C-code 為軸：BD/Gemini 的 l1_code 是域機器值、l2_code 是 C-code；Claude 的 l1/l2 都是 C-code。
    label 補全順序：模型自帶 → BD/Gemini 聯集 map → ai_judge（domain_label / path_label 末段）。
    """
    from app.core.judge_config import ai_judge

    # L1：取域機器值（attributions.l1_code 慣例）。BD/Gemini 已是機器值；Claude 是 C-1..C-6 → 反查。
    raw_l1 = a.get("l1_code") or a.get("l1") or ""
    l1_domain = _L1_CODE_TO_DOMAIN.get(raw_l1, raw_l1)  # 已是機器值則原樣
    l1_label = a.get("l1_label") or ai_judge.domain_label(l1_domain)

    l2_code = a.get("l2_code") or a.get("l2") or ""
    l2_label = a.get("l2_label") or l2_lab.get(l2_code) or ""
    if not l2_label and l2_code:  # ai_judge fallback：完整路徑末段＝葉 label
        l2_label = (ai_judge.path_label(l2_code).split("›")[-1] or "").strip()
    return l1_domain, l1_label, l2_code, l2_label


def _to_attributions(
    r: dict, l1_lab: dict[str, str], l2_lab: dict[str, str]
) -> list[dict]:
    """一則評論的模型結果 → attribution_history snapshot_of 形狀陣列（每 attr 一筆）。

    polarity/sentiment_score 為評論級（同則各 attr 相同）；stage 固定 'judged'（評測即判到底）；
    l3 留空（評測只到 L2）；confidence 取 attr.conf（Claude 無 → 0.8 佔位）；finding_id 走
    pipeline 多歸因慣例 `fd_{source}_{sid}__{l1_domain}`（history 快照僅供顯示/去重排序，notes 不 join）。
    """
    polarity = r.get("polarity")
    sentiment = r.get("sentiment")
    sid = str(r["rec_oid"])
    attrs = r.get("attrs") or []
    out: list[dict] = []
    for i, a in enumerate(attrs):
        l1_domain, l1_label, l2_code, l2_label = _norm_attr(a, l1_lab, l2_lab)
        out.append(
            {
                "finding_id": f"fd_product_reviews_{sid}__{l1_domain}",
                "polarity": polarity,
                "sentiment_score": sentiment,
                "stage": "judged",
                "l1": {"code": l1_domain, "label": l1_label},
                "l2": {"code": l2_code, "label": l2_label},
                "l3": {"code": None, "label": None},
                "confidence": {"value": a.get("conf", 0.8), "raw": None, "tier": None},
                "content": {"summary": None, "evidence": None, "action": None},
                "is_primary": bool(a.get("primary")) or (i == 0),
            }
        )
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", default="/app/tmp/multi_model", help="多模型 v4 JSON 目錄")
    ap.add_argument(
        "--files",
        nargs="*",
        default=["bytedance_v4.json", "gemini_v4.json"],
        help="要灌入的模型檔（相對 --dir）——僅專案已配置的 provider 模型（ByteDance/Gemini）；"
        "claude-fable-5 是 workflow 手動評測非配置 LLM，預設不灌（要對比可顯式加 claude_v4.json）",
    )
    ap.add_argument("--source", default="product_reviews")
    ap.add_argument("--triggered-by", default="multimodel_backfill")
    ap.add_argument("--job-id", default="multimodel_v4_backfill")
    ap.add_argument("--dry-run", action="store_true", help="只統計不寫入")
    args = ap.parse_args()

    from app.core.db import tables as T
    from app.core.db.attribution_history import insert_prejudge_event

    base = Path(args.dir)
    files = [base / f for f in args.files]
    missing = [str(f) for f in files if not f.exists()]
    if missing:
        print(f"❌ 找不到檔案：{missing}", file=sys.stderr)
        return 2

    l1_lab, l2_lab = _build_label_maps(files)
    print(f"label map：L1 {len(l1_lab)} 域名、L2 {len(l2_lab)} 葉")

    grand_ins = grand_skip = 0
    for f in files:
        d = json.loads(f.read_text(encoding="utf-8"))
        model = d["model"]
        results = d.get("results", [])
        # params 帶來源標記＝去重維度一環（同模型同來源重跑 skip；換版本可改 params 強制新列）
        params = {"origin": "multi_model_eval", "set": "v4"}
        ins = skip = 0
        if args.dry_run:
            attributed = sum(1 for r in results if r.get("attrs"))
            print(f"[dry-run] {model}：{len(results)} 則（{attributed} 則有歸因）")
            continue
        with T.get_engine().begin() as c:
            for r in results:
                attributions = _to_attributions(r, l1_lab, l2_lab)
                if not attributions:
                    # 純好評（無歸因）也記一筆初判快照（空陣列）→ 歷史看得到「該模型判為 non_issue」。
                    attributions = []
                wrote = insert_prejudge_event(
                    c,
                    args.source,
                    str(r["rec_oid"]),
                    model=model,
                    model_votes=None,
                    params=params,
                    attributions=attributions,
                    job_id=args.job_id,
                    triggered_by=args.triggered_by,
                )
                ins += int(wrote)
                skip += int(not wrote)
        print(f"✅ {model}：寫入 {ins}、去重 skip {skip}（共 {len(results)}）")
        grand_ins += ins
        grand_skip += skip

    if not args.dry_run:
        print(f"\n總計：寫入 {grand_ins}、skip {grand_skip}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

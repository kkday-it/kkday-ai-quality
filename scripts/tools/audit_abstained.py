#!/usr/bin/env python3
"""靜默漏判抽查 — 撈「判了非正向、六域卻全部棄權」的反饋，讓漏判從隱形變成可見。

定位（補的是一個**結構性**盲區，不是又一個一致率指標）：
  `taxonomy_health.py` 自述「能診斷現有類好不好用，無法回答缺什麼類」；而多模型一致率
  對這池子的敏感度是**零**——全部棄權在一致率上是「三方 100% 一致」，永遠不進分歧清單。
  六域全平行、每域只判自己（`domain_router` 未啟用），A 域說「不屬本項」而無 B 域認領時，
  該類反饋就落進無歸因、**零報錯**。覆蓋斷裂造成的漏判全部沉澱在這裡。

為什麼這池子的人工成本特別低：它的真值是「六域皆棄權」，所以**任何從棄權變成有歸因的
案例，數學上不可能被算成多報**——人只需判「這條歸因對不對」，不需判「該不該有」。

⚠️ 過濾條件是「非正向 × 無 l1_code」，不是 `prejudge_stage='pending_data'`：後者實測
全庫僅個位數，絕大多數漏判的 stage 是 `judged`／`pending_review`（管線正常跑完、只是沒歸出因）。

用法（scripts/ 已 bind mount，免 docker cp）：
    docker compose -f docker-compose.dev.yml exec -T backend \
        python /app/scripts/tools/audit_abstained.py --n 40 --out /app/tmp/abstained.json

    # 只看關鍵詞分布、不抽樣（決定下一輪補哪個覆蓋洞的優先序）
    docker compose -f docker-compose.dev.yml exec -T backend \
        python /app/scripts/tools/audit_abstained.py --terms-only

唯讀，不打 LLM，秒級完成。抽樣以 md5(source_id) 排序 → 同參數跨次可重現、可比對。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter

_BACKEND = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "backend")
)
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from app.core import source_mapping as srcmap
from app.core.db.ingest import get_items_by_ids
from app.core.db.tables import get_engine
from sqlalchemy import text

# 主題詞表：用途是**排優先序**（哪個覆蓋洞最常見），不是判定歸屬。
# 這裡刻意用字面比對——它統計的是「這批文字在談什麼」，不是在判準上做語義裁決
# （語義判準禁寫成字面清單，見 .claude/rules/prompt-authoring.md 六之三；本表不屬判準）。
_TERMS: dict[str, tuple[str, ...]] = {
    "網路連線": (
        "網速",
        "訊號",
        "連線",
        "上網",
        "斷線",
        "eSIM",
        "SIM",
        "漫遊",
        "流量",
        "限速",
        "降速",
    ),
    "設定開通": ("設定", "開通", "啟用", "APN", "掃碼", "教學", "指引", "說明書"),
    "兌換取票": ("兌換", "換票", "取票", "核銷", "憑證", "QR", "序號"),
    "餐飲": ("食物", "餐", "菜", "甜品", "份量", "口味", "難吃", "好吃"),
    "人員服務": ("導遊", "司機", "員工", "服務", "態度", "領隊"),
    "時間節奏": ("遲到", "等待", "排隊", "時間", "停留", "集合", "趕"),
    "設施環境": ("設施", "環境", "冷氣", "廁所", "座位", "車輛", "房間", "髒"),
    "費用": ("價格", "費用", "貴", "收費", "退款", "扣款"),
    "人潮": ("人多", "擁擠", "人太多", "太多人"),
}

_SQL = """
SELECT source_id, feedback_source_code, polarity, prejudge_stage, sentiment_score
FROM attribution_tbl
WHERE is_deleted IS NOT TRUE
  AND coalesce(polarity, '') <> 'positive'
  AND coalesce(l1_code, '') = ''
  {src_filter}
ORDER BY md5(source_id)
"""


def _fetch_pool(source: str | None) -> list[dict]:
    """撈整池（不分頁）——全庫規模在數百量級，一次抓完才能算全池的關鍵詞分布。"""
    sql = _SQL.format(src_filter="AND feedback_source_code = :src" if source else "")
    with get_engine().connect() as conn:
        rows = conn.execute(text(sql), {"src": source} if source else {}).fetchall()
    return [
        {
            "source_id": str(r[0]),
            "source": r[1],
            "polarity": r[2],
            "stage": r[3],
            "sentiment": r[4],
        }
        for r in rows
    ]


def _attach_text(pool: list[dict]) -> None:
    """依來源分組取回原文並就地填入 title/content（走 source_mapping，欄名改了會自動跟上）。"""
    by_src: dict[str, list[dict]] = {}
    for row in pool:
        by_src.setdefault(row["source"], []).append(row)
    for src, rows in by_src.items():
        if src not in srcmap.sources():
            continue
        items = {
            str(it.get(k) or ""): it
            for it in get_items_by_ids([r["source_id"] for r in rows], source=src)
            for k in ("rec_oid", "session_oid", "source_id")
            if it.get(k)
        }
        for row in rows:
            item = items.get(row["source_id"])
            canon = srcmap.normalize_row(src, item) if item else {}
            row["title"] = canon.get("title") or ""
            row["content"] = canon.get("content") or ""


def _body(row: dict) -> str:
    return (row.get("title") or "") + (row.get("content") or "")


def _term_hits(rows: list[dict]) -> Counter:
    """一則可命中多個主題——目的是看「哪類問題最常掉進池子」，不是互斥分類。"""
    hits: Counter = Counter()
    for row in rows:
        body = _body(row)
        for topic, words in _TERMS.items():
            if any(w in body for w in words):
                hits[topic] += 1
    return hits


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--n", type=int, default=30, help="抽樣則數（0＝全部輸出）")
    ap.add_argument("--source", help="限定來源（reviews / conversations…），預設全部")
    ap.add_argument(
        "--min-len",
        type=int,
        default=8,
        help="內容過短者不抽——空泛短句本來就該棄權，不是漏判",
    )
    ap.add_argument(
        "--terms-only", action="store_true", help="只印關鍵詞分布，不抽樣不輸出檔案"
    )
    ap.add_argument("--out", help="抽樣結果 JSON 落點（供逐則裁決）")
    args = ap.parse_args()

    pool = _fetch_pool(args.source)
    if not pool:
        raise SystemExit(
            "池子是空的——若非預期，先確認 attribution_tbl 有資料且 polarity 已回填。"
        )
    _attach_text(pool)

    print(f"靜默漏判池：{len(pool)} 筆（非正向且六域全棄權）")
    print(f"  來源分布　{dict(Counter(r['source'] for r in pool).most_common())}")
    print(f"  傾向分布　{dict(Counter(r['polarity'] for r in pool).most_common())}")
    print(f"  階段分布　{dict(Counter(r['stage'] for r in pool).most_common())}")
    # 分母只算取得到原文的：孤兒列（歸因還在、來源列已被抽換）什麼詞都不會命中，
    # 混進分母會同時壓低每個主題的比例、又把「未落入任何主題」灌成假高峰。
    texted = [r for r in pool if _body(r)]
    orphan = [r for r in pool if not _body(r)]
    if orphan:
        byo = dict(Counter(r["source"] for r in orphan).most_common())
        print(
            f"  ⚠️ 孤兒歸因 {len(orphan)} 筆 {byo}——歸因列還在、來源列已不存在（來源表被抽換）"
        )
        print("     這不是漏判，是資料完整性問題；已排除於下方統計")

    print(f"\n── 主題分布（分母＝取得到原文的 {len(texted)} 則；一則可命中多項）──")
    print(
        "   ⚠️ 這是**提及率**不是漏判率：詞表不分褒貶，「導遊很棒」同樣命中「人員服務」。"
    )
    print("      用途是縮小人工抽查的搜尋範圍，不能直接當成「該主題有 N 則漏判」。")
    hits = _term_hits(texted)
    for topic, n in hits.most_common():
        print(
            f"  {topic:8} {n:4} 則  {n / max(1, len(texted)):5.1%}  {'█' * round(n / max(1, len(texted)) * 40)}"
        )
    uncovered = [
        r
        for r in texted
        if not any(any(w in _body(r) for w in ws) for ws in _TERMS.values())
    ]
    print(
        f"  （未落入任何主題 {len(uncovered)} 則＝{len(uncovered) / max(1, len(texted)):.1%}——主題詞表未涵蓋，值得優先人工看）"
    )

    if args.terms_only:
        return

    # 抽樣：已依 md5(source_id) 排序，直接取前 n 則即為可重現樣本
    cand = [
        r
        for r in pool
        if len((r.get("content") or "") + (r.get("title") or "")) >= args.min_len
    ]
    sample = cand if args.n <= 0 else cand[: args.n]
    print(
        f"\n── 抽樣 {len(sample)}/{len(cand)} 則（內容 ≥{args.min_len} 字者才入選）──"
    )
    for i, r in enumerate(sample, 1):
        body = re.sub(
            r"\s+", " ", f"{r.get('title') or ''} ｜ {r.get('content') or ''}"
        ).strip()
        print(
            f"  [{i:02d}] {r['source_id']} {r['source']:14} {r['polarity']:9} {body[:120]}"
        )

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(sample, fh, ensure_ascii=False, indent=1)
        print(f"\n→ {args.out}")
    print(
        "\n下一步：逐則判「這則該不該有歸因、該歸哪個 facet」。判為該收者即為覆蓋斷裂，"
        "\n補法是加寬**目標域**的判定範圍語彙（各域看不到彼此，在來源域加指路無效），"
        "\n完成後到 verify_coverage.py 的 CASES 登記成對案例（正向＋反向守備）。"
    )


if __name__ == "__main__":
    main()

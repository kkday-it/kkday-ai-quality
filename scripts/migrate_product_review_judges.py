#!/usr/bin/env python3
"""一次性遷移：judgments(source='product_reviews') → product_reviews.judges/review_polarity/judged_at。

多歸因改造 P1：把既有單一 finding（判決結果存在 judgments 表）遷成 product_reviews 自帶的
judges 單元素陣列 + review_polarity + judged_at。保留 judgments 舊列不刪（稽核 / 回滾）；P2 上線後
product_reviews 來源改由 db.upsert_review_judges 整欄覆寫，與本腳本寫入的單元素陣列相容（重判自然取代）。
owner 留空，待業務 owner mapping 拍板後另跑 backfill。

只有「負向 + 有 L1 域」才成一條違規歸因（單元素陣列）；正向 / 中性 / 無法歸類一律空陣列 []，
但 review_polarity / judged_at 仍寫入（＝已判，別於未判 NULL）。

冪等：以 item_id 整欄覆寫；重跑用 judgments 當前內容覆蓋，不疊加。sqlalchemy 為重庫，函式內 lazy import。

用法（後端環境）：
    python scripts/migrate_product_review_judges.py           # dry-run，只報數不寫
    python scripts/migrate_product_review_judges.py --apply    # 實際寫回 product_reviews
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _to_judge(finding: dict) -> dict:
    """單一 finding dict → 單元素 ReviewJudge dict（欄位對映；遷移的單 finding 即主歸因）。"""
    l1 = finding.get("l1_domain_code", "")
    return {
        "judge_id": l1 or "none",
        "l1_domain_code": l1,
        "l1_label": finding.get("l1_label", ""),
        "l2_code": finding.get("l2_code", ""),
        "l2_label": finding.get("l2_label", ""),
        "l3_code": finding.get("l3_code", ""),
        "l3_label": finding.get("l3_label", ""),
        "confidence": finding.get("confidence", 0.0),
        "raw_confidence": finding.get("raw_confidence", 0.0),
        "confidence_tier": finding.get("confidence_tier", ""),
        "judgment_stage": finding.get("judgment_stage", ""),
        "recommended_action": finding.get("recommended_action", ""),
        "owner": "",  # 待 owner mapping 拍板後 backfill
        "evidence_quote": finding.get("evidence_quote", ""),
        "problem_summary": finding.get("problem_summary", ""),
        "is_primary": True,
        "is_enhanced": bool(finding.get("is_enhanced", False)),
        "enhance_model": finding.get("enhance_model", ""),
        "model_used": finding.get("model_used", ""),
        "judged_at": finding.get("judged_at", ""),
    }


def main() -> int:
    """掃 judgments(source=product_reviews)，逐筆遷成 product_reviews.judges；--apply 才寫回。"""
    apply = "--apply" in sys.argv
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
    try:
        from sqlalchemy import select

        from app.core import db
        from app.core import tables as T
    except ImportError as exc:
        print(f"需在後端環境執行（缺 {exc.name}）", file=sys.stderr)
        return 1

    engine = T.get_engine()
    with engine.connect() as c:
        rows = list(
            c.execute(
                select(T.judgments.c.item_id, T.judgments.c.data).where(
                    T.judgments.c.source == "product_reviews"
                )
            )
        )

    migrated = attributed = miss = 0
    for item_id, raw in rows:
        try:
            finding = json.loads(raw) if raw else {}
        except (ValueError, TypeError):
            finding = {}
        pol = finding.get("polarity") or ""
        l1 = finding.get("l1_domain_code", "")
        judges = [_to_judge(finding)] if pol == "negative" and l1 else []
        if judges:
            attributed += 1
        judged_at = finding.get("judged_at") or finding.get("created_at") or ""
        if apply:
            hit = db.upsert_review_judges(item_id, judges, review_polarity=pol, judged_at=judged_at)
            if hit:
                migrated += 1
            else:
                miss += 1  # judgments 有此 item_id 但 product_reviews 查無（拆表不一致）
        else:
            migrated += 1

    verb = "已遷移" if apply else "待遷移（dry-run）"
    tail = f"｜product_reviews 查無 {miss} 列（未寫入）" if miss else ""
    print(
        f"judgments(source=product_reviews) 共 {len(rows)} 列｜{verb} {migrated}"
        f"｜其中有效違規歸因 {attributed} 條{tail}"
    )
    if not apply:
        print("→ 確認無誤後加 --apply 實際寫回")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""一次性資料遷移：把 intake_items 內既有 source='product_reviews' 的列灌進新 product_reviews 專表。

背景：product_reviews 拆表（見 alembic 3771110d1d2d）後，schema 已就緒但既有資料仍全數
留在 intake_items（該 migration 刻意不含 backfill，見計畫）。本腳本讀 intake_items 的
raw JSON（原始上傳列全文）→ 用 product_reviews_ingest.row_to_product_review 重新映射
→ db.insert_product_reviews_batch 灌新表（冪等 upsert，可重複執行安全覆蓋）。

使用時機：確認 alembic upgrade head 已套用（product_reviews 表已建立）後，人工執行一次；
執行後建議人工抽查新表筆數 / 抽樣內容是否與原 intake_items 一致，再考慮是否清空
intake_items 內對應舊列（本腳本刻意不做清空，避免雙重破壞性操作疊加難以回溯）。

注意事項：
- 本腳本讀 raw JSON 重新解析，若原始上傳列的 raw 欄缺失/損毀，該筆會被跳過並記錄警告，
  不中斷整批（見 --dry-run 檢視警告清單，正式執行前建議先跑一次 dry-run 確認規模）。
- ⚠️ 本次交付僅負責撰寫本腳本，禁止在本次任務中實際執行（見任務說明）。

用法：python scripts/backfill_product_reviews.py [--dry-run]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "backend"))

from sqlalchemy import select  # noqa: E402

from app.core import db  # noqa: E402
from app.core import source_mapping as srcmap  # noqa: E402
from app.core import tables as T  # noqa: E402
from app.judge.ingest import product_reviews as product_reviews_ingest  # noqa: E402

_SOURCE = "product_reviews"


def _load_rows() -> tuple[list[dict], int]:
    """讀 intake_items 內 source='product_reviews' 的列，解析 raw JSON → 專表欄位 dict。

    Returns:
        (rows, skipped)：rows 為可直接灌 insert_product_reviews_batch 的 dict 清單；
        skipped 為 raw 損毀 / 缺失而跳過的筆數。
    """
    ii = T.intake_items
    stmt = select(ii).where(ii.c.source == _SOURCE)
    rows: list[dict] = []
    skipped = 0
    with T.get_engine().connect() as c:
        for r in c.execute(stmt).mappings():
            raw_json = r.get("raw")
            if not raw_json:
                skipped += 1
                continue
            try:
                raw = json.loads(raw_json)
            except (ValueError, TypeError):
                skipped += 1
                continue
            canon = srcmap.normalize_row(_SOURCE, raw)
            rows.append(product_reviews_ingest.row_to_product_review(canon, raw))
    return rows, skipped


def main() -> None:
    """執行 backfill（--dry-run 僅印計畫不寫入）。"""
    dry = "--dry-run" in sys.argv
    rows, skipped = _load_rows()
    print(f"計畫：灌入 {len(rows)} 筆 product_reviews（intake_items raw 損毀跳過 {skipped} 筆）")
    if dry:
        for row in rows[:5]:
            print(f"  item_id={row.get('item_id')} source_record_id={row.get('source_record_id')}")
        print("（--dry-run，未寫入）")
        return
    inserted = db.insert_product_reviews_batch(rows)
    print(f"完成：product_reviews upsert {inserted} 筆")


if __name__ == "__main__":
    main()

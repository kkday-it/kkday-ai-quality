#!/usr/bin/env python
"""資料保留期清理（維運操作）：依 `config/global/retention.json` 清掉過期的觀測型資料。

**預設 dry-run**：只報「會刪幾列 / 釋出多少空間」，不動任何資料；要真的執行加 `--apply`。
這是刻意的——保留期清理不可逆，跑錯一次沒有復原路徑（本腳本不備份，備份請走 pg_dump）。

清理範圍（皆為**觀測型／可重生**資料，業務真相不在其中）：

1. `llm_usage_lst`      — 刪除超過保留期的 per-call 用量列
2. `prejudge_run_tbl`   — 把超過保留期的 `log` 欄置 NULL（**保留統計列**，不刪 run）
3. `attribution_event_lst`
   · 刪除「已被後續成功初判取代」的 failure 事件
   · 每 (來源, 評論, 模型) 只保留最新 N 筆 prejudge 快照
4. `data/prompt_debug_batch/<run_id>/` — 刪除超過保留期的跑批產物目錄

刻意**不碰**的：`note` 事件（人工輸入）、`attribution_tbl`（當前初判結果）、
`judge_rule_version_lst`（版本庫 append-only）、5 張來源鏡像表（原始反饋資料）。

本檔的 DDL 級操作（window function 排名、相關子查詢 EXISTS）走原生 SQL 而非 Table 物件：
清理條件是集合運算而非 CRUD，Core 表達反而更難讀。⚠️ 代價是表名／欄名改動不會自動跟上，
改 schema 時務必回頭核對此處字串（欄名一律用 DB 規範名，不是 tables.py 的 Python key）。

用法（一律在容器內跑，見專案環境鐵律）：
    docker compose -f docker-compose.dev.yml exec backend \\
        python /app/scripts/ops/retention.py            # dry-run，只報不刪
    docker compose -f docker-compose.dev.yml exec backend \\
        python /app/scripts/ops/retention.py --apply    # 真的執行
    ... --only llm_usage_lst,prejudge_run_tbl           # 只跑指定項目
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, "/app/backend")

from sqlalchemy import text  # noqa: E402

from app.core import paths  # noqa: E402
from app.core.db import tables as T  # noqa: E402

_POLICY_FILE = Path(paths.CONFIG_DIR) / "global" / "retention.json"


def _policy() -> dict:
    """讀保留期政策（config/global/retention.json）。"""
    return json.loads(_POLICY_FILE.read_text(encoding="utf-8"))


def _cutoff(days: int) -> datetime:
    """回推 N 天的 UTC 時間點。"""
    return datetime.now(timezone.utc) - timedelta(days=days)


def _report(name: str, n: int, unit: str, *, applied: bool) -> None:
    verb = "已清理" if applied else "將清理"
    mark = "·" if n else " "
    print(f"  {mark} {name:38} {verb} {n:>7,} {unit}")


def clean_llm_usage(conn, pol: dict, *, applied: bool) -> int:
    """刪除超過保留期的 per-call 用量列。"""
    days = pol["llm_usage_lst"]["delete_older_than_days"]
    cut = _cutoff(days)
    n = conn.execute(
        text("SELECT count(*) FROM llm_usage_lst WHERE create_date < :cut"), {"cut": cut}
    ).scalar()
    if applied and n:
        conn.execute(text("DELETE FROM llm_usage_lst WHERE create_date < :cut"), {"cut": cut})
    _report(f"llm_usage_lst（逾 {days} 天）", n, "列", applied=applied)
    return n


def clean_run_logs(conn, pol: dict, *, applied: bool) -> int:
    """把超過保留期的 run_log 置 NULL（保留統計列本身）。"""
    days = pol["prejudge_run_tbl"]["null_log_older_than_days"]
    cut = _cutoff(days)
    n = conn.execute(
        text("SELECT count(*) FROM prejudge_run_tbl WHERE log IS NOT NULL AND create_date < :cut"),
        {"cut": cut},
    ).scalar()
    if applied and n:
        conn.execute(
            text(
                "UPDATE prejudge_run_tbl SET log = NULL WHERE log IS NOT NULL AND create_date < :cut"
            ),
            {"cut": cut},
        )
    _report(f"prejudge_run_tbl.log（逾 {days} 天置 NULL）", n, "列", applied=applied)
    return n


def clean_superseded_failures(conn, pol: dict, *, applied: bool) -> int:
    """刪除「該評論之後已成功初判」的 failure 事件——查詢條件永遠讀不到它們。"""
    if not pol["attribution_event_lst"].get("delete_superseded_failures"):
        return 0
    # 同 (來源, 評論) 存在時間更晚的 prejudge 事件 → 該 failure 已被取代
    cond = """
        FROM attribution_event_lst f
        WHERE f.kind = 'failure' AND EXISTS (
            SELECT 1 FROM attribution_event_lst ok
            WHERE ok.kind = 'prejudge'
              AND ok.feedback_source_code = f.feedback_source_code
              AND ok.source_id = f.source_id
              AND ok.create_date > f.create_date
        )
    """
    n = conn.execute(text(f"SELECT count(*) {cond}")).scalar()
    if applied and n:
        conn.execute(
            text(
                "DELETE FROM attribution_event_lst WHERE attribution_event_oid IN "
                f"(SELECT f.attribution_event_oid {cond})"
            )
        )
    _report("attribution_event_lst 已取代的 failure", n, "列", applied=applied)
    return n


def clean_old_snapshots(conn, pol: dict, *, applied: bool) -> int:
    """每 (來源, 評論, 模型) 只保留最新 N 筆 prejudge 快照。"""
    keep = pol["attribution_event_lst"].get("keep_prejudge_snapshots_per_model")
    if not keep:
        return 0
    ranked = """
        FROM (
            SELECT attribution_event_oid,
                   row_number() OVER (
                       PARTITION BY feedback_source_code, source_id, model
                       ORDER BY create_date DESC, attribution_event_oid DESC
                   ) AS rn
            FROM attribution_event_lst WHERE kind = 'prejudge'
        ) r WHERE r.rn > :keep
    """
    n = conn.execute(text(f"SELECT count(*) {ranked}"), {"keep": keep}).scalar()
    if applied and n:
        conn.execute(
            text(
                "DELETE FROM attribution_event_lst WHERE attribution_event_oid IN "
                f"(SELECT r.attribution_event_oid {ranked})"
            ),
            {"keep": keep},
        )
    _report(f"attribution_event_lst 舊快照（每組保 {keep} 筆）", n, "列", applied=applied)
    return n


def clean_batch_artifacts(pol: dict, *, applied: bool) -> int:
    """刪除超過保留期的跑批產物目錄。"""
    days = pol["prompt_debug_batch_artifacts"]["delete_dirs_older_than_days"]
    root = Path(paths.DATA_DIR) / "prompt_debug_batch"
    if not root.is_dir():
        _report(f"跑批產物目錄（逾 {days} 天）", 0, "個", applied=applied)
        return 0
    deadline = time.time() - days * 86400
    stale = [d for d in root.iterdir() if d.is_dir() and d.stat().st_mtime < deadline]
    if applied:
        for d in stale:
            shutil.rmtree(d, ignore_errors=True)
    _report(f"跑批產物目錄（逾 {days} 天）", len(stale), "個", applied=applied)
    return len(stale)


_DB_TASKS = {
    "llm_usage_lst": clean_llm_usage,
    "prejudge_run_tbl": clean_run_logs,
    "attribution_event_lst": lambda c, p, applied: (
        clean_superseded_failures(c, p, applied=applied)
        + clean_old_snapshots(c, p, applied=applied)
    ),
}


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--apply", action="store_true", help="真的執行（預設只報不刪）")
    ap.add_argument("--only", default="", help="只跑指定項目（逗號分隔；可用 DB 表名或 artifacts）")
    args = ap.parse_args()

    pol = _policy()
    only = {s.strip() for s in args.only.split(",") if s.strip()}
    applied = args.apply

    print(f"\n📋 資料保留期清理（{'執行模式' if applied else 'dry-run —— 不會動任何資料'}）")
    print(f"   政策檔：{_POLICY_FILE}\n")

    total = 0
    with T.get_engine().begin() as conn:
        for name, fn in _DB_TASKS.items():
            if only and name not in only:
                continue
            total += fn(conn, pol, applied=applied)
    if not only or "artifacts" in only:
        total += clean_batch_artifacts(pol, applied=applied)

    print()
    if not applied and total:
        print(f"   共 {total:,} 項可清理。確認無誤後加 --apply 執行。\n")
    elif applied:
        print(f"   ✅ 共清理 {total:,} 項。\n")
    else:
        print("   ✅ 無可清理項目（皆在保留期內）。\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

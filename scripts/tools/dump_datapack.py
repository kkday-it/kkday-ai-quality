#!/usr/bin/env python3
"""全庫資料包（datapack）匯出 CLI —— 產生前台可安全匯入的純資料包（非 SQL）。

打包邏輯共用後端 `app.core.db.datapack.build_datapack`（CLI 與匯出端點同一套，不重造）。輸出 zip：
    manifest.json              # format/schema_version + 每表 row_count / sha256
    tables/<table>.ndjson      # 每行一筆 JSON；JSONB 存原生物件、DateTime(tz) 存 ISO 字串

分發：把 zip 放網盤 / GitHub Release，別人於前台「資料導入」上傳即可（亦可前台「導出」直接下載）。

用法：
  python scripts/tools/dump_datapack.py                      # → data/exports/datapack_<ts>.zip（不含敏感表）
  python scripts/tools/dump_datapack.py --include-sensitive  # 併入 settings（含機密）
  python scripts/tools/dump_datapack.py --tables attributions,product_reviews
  python scripts/tools/dump_datapack.py --out /path/to/x.zip
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

# repo 根：scripts/tools/dump_datapack.py → parents[2]；backend 掛 sys.path 才能 import app.*
_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "backend"))

from app.core.db import datapack as DP  # noqa: E402


def main() -> int:
    """解析參數 → build_datapack → 寫檔。回 0 成功。"""
    ap = argparse.ArgumentParser(description="全庫資料包匯出")
    ap.add_argument("--out", type=Path, default=None, help="輸出 zip 路徑（預設 data/exports/datapack_<ts>.zip）")
    ap.add_argument("--include-sensitive", action="store_true", help="併入 settings（含機密）")
    ap.add_argument("--tables", type=str, default="", help="只匯出指定表（逗號分隔）；預設全部")
    args = ap.parse_args()

    only = [t for t in args.tables.split(",") if t] if args.tables else None
    tables = DP.resolve_export_tables(include_sensitive=args.include_sensitive, only=only)
    if not tables:
        print("❌ 無可匯出的表（檢查 --tables 拼字）", file=sys.stderr)
        return 1

    ts = datetime.now(timezone.utc)
    out = args.out or (_ROOT / "data" / "exports" / f"kkday-ai-quality-datapack-{ts:%Y%m%d%H%M}.zip")
    out.parent.mkdir(parents=True, exist_ok=True)

    print(f"▶ 匯出 {len(tables)} 張表：{'、'.join(tables)}")
    data = DP.build_datapack(include_sensitive=args.include_sensitive, only=only, generated_at=ts)
    out.write_bytes(data)

    size_mb = len(data) / 1024 / 1024
    print(f"✓ 完成：{out}（{size_mb:.1f} MB，schema={DP.current_alembic_head()}）")
    if not args.include_sensitive:
        print("ℹ️ 未含敏感表（settings）；需要請加 --include-sensitive")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

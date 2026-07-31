#!/usr/bin/env python3
"""一次性遷移：跑批落盤產物 `summary.json` 的舊鍵名改為新鍵名（冪等，可重複執行）。

## 為什麼需要這支

2026-07-31 的跑批重構把 `ok` 這個一名四義的欄位拆成三個各自明確的名字
（見 `backend/app/judge/prompt_debug_batch.py` 的 `_is_success` 檔頭）：

| 語義 | 舊鍵 | 新鍵 |
|---|---|---|
| 累計成功**筆數** | `ok` | `ok_count` |
| **單筆**列結果 | `recent[].ok` | `recent[].succeeded` |

但**已經落盤的 `summary.json` 沒有跟著改**。`_disk_summary()` 對「已有 summary.json」的 run 是
原樣回傳整份檔案（那是刻意的：收尾當下的快照就是最權威的紀錄），於是前端讀 `ok_count` 拿到
undefined、`list_runs` 的 `.get("ok_count", 0)` 兜底成 0——**所有歷史 run 都顯示「成功 0」**，
最近完成明細每一列都被畫成紅色 ✗。資料其實都在，純粹是鍵名改了沒遷移。

依專案鐵律「不留相容分支，用一次性 migration 取代」：**不在 `_disk_summary` 加讀取端相容判斷**，
而是把落盤產物一次改到位。這支就是那個 migration。

## 用法

```bash
python3 scripts/ops/migrate_batch_summary_keys.py            # 預覽（不寫檔）
python3 scripts/ops/migrate_batch_summary_keys.py --apply    # 實際寫入
python3 scripts/ops/migrate_batch_summary_keys.py --apply --data-dir /path/to/data
```

冪等：已是新鍵名的檔案直接跳過；重複跑不會有副作用。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 專案預設 data 目錄（與 backend `app.core.paths.DATA_DIR` 同一處；這裡刻意不 import app.*，
# 讓這支腳本免 venv、免起容器就能跑）。
_DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
_BATCH_SUBDIR = "prompt_debug_batch"


def migrate_summary(summary: dict) -> tuple[dict, list[str]]:
    """把單份 summary 的舊鍵改成新鍵。

    Args:
        summary: `summary.json` 解析後的 dict（原地不改，回傳新物件）。

    Returns:
        `(遷移後的 dict, 改了哪些項目的說明清單)`；說明為空＝本來就是新格式。
    """
    out = dict(summary)
    changed: list[str] = []

    if "ok" in out and "ok_count" not in out:
        out["ok_count"] = out.pop("ok")
        changed.append(f"ok → ok_count（{out['ok_count']}）")
    elif "ok" in out:
        # 兩個都有：新鍵已存在，舊鍵是殘留 → 直接丟掉，以新鍵為準
        out.pop("ok")
        changed.append("移除殘留的舊鍵 ok")

    recent = out.get("recent")
    if isinstance(recent, list):
        hits = 0
        new_recent = []
        for item in recent:
            if isinstance(item, dict) and "ok" in item:
                fixed = dict(item)
                value = fixed.pop("ok")
                fixed.setdefault("succeeded", bool(value))
                new_recent.append(fixed)
                hits += 1
            else:
                new_recent.append(item)
        if hits:
            out["recent"] = new_recent
            changed.append(f"recent[].ok → succeeded（{hits} 筆）")

    return out, changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="實際寫入；未給＝只預覽")
    parser.add_argument("--data-dir", type=Path, default=_DEFAULT_DATA_DIR, help="data 目錄（預設 repo 根的 data/）")
    args = parser.parse_args()

    batch_dir = args.data_dir / _BATCH_SUBDIR
    if not batch_dir.is_dir():
        print(f"找不到跑批目錄：{batch_dir}（沒有歷史 run 就不需要遷移）")
        return 0

    scanned = migrated = skipped = failed = 0
    for summary_file in sorted(batch_dir.glob("*/summary.json")):
        scanned += 1
        try:
            data = json.loads(summary_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            failed += 1
            print(f"⚠️  讀取失敗，略過：{summary_file.parent.name}（{exc}）")
            continue

        fixed, changed = migrate_summary(data)
        if not changed:
            skipped += 1
            continue

        migrated += 1
        print(f"{'✅' if args.apply else '·'} {summary_file.parent.name}：{'；'.join(changed)}")
        if args.apply:
            # 原子寫入：先寫 .tmp 再 replace，避免中途中斷留下半份檔案
            temp = summary_file.with_suffix(".json.tmp")
            temp.write_text(json.dumps(fixed, ensure_ascii=False, indent=2), encoding="utf-8")
            temp.replace(summary_file)

    print(
        f"\n掃描 {scanned} 份｜需遷移 {migrated}｜已是新格式 {skipped}｜讀取失敗 {failed}"
        + ("" if args.apply else "\n（預覽模式，未寫入。加 --apply 實際執行）")
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

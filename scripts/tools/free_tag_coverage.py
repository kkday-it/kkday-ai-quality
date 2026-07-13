"""free tag 匯總表 → free_tag_mapping.json 加權覆蓋率驗收腳本。

口徑（與 backend/app/core/db/comparison.py 對齊）：
- 顯式命中：tag_name 在 mapping 且 l1/l2 非空
- 顯式無對應：tag_name 在 mapping 但 l1/l2 皆空（建議/其他等非歸因性總評，視為「已處理」）
- 子字串兜底：tag_name 與任一 L2 label 互為子字串

驗收線：加權處理覆蓋率 ≥ 95%（計畫 §6-1；基線 61.0% @ 2026-07-11 重構前）。

用法：
    python scripts/tools/free_tag_coverage.py --csv "~/Downloads/free tag 匯總表.csv" [--top 30]

CSV 欄位：tag_name, tag_cnt, avg_value, merged_tag_list（來源＝外部評論系統匯總導出，
檔案 18MB 不入版控；2026-07-11 版 sha256 起首 = 見當日計畫記錄）。
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MAPPING_PATH = REPO_ROOT / "config" / "ai_judge" / "free_tag_mapping.json"

# L2 label 全集（子字串兜底用；與 docs/prompts/prompts/*.md facet_catalog 同步維護，
# SSOT＝app.judge.prompt_source.structure()，原 rule_C-1~C-6 已於 2026-07-13 退役）
L2_LABELS = [
    # C-1 商品內容
    "商品定位", "行程流程", "費用資訊", "集合資訊", "使用／兌換", "限制與風險", "退改與服務承諾",
    # C-2 商品品質
    "網路品質", "餐飲品質", "車輛設備", "住宿品質", "設施設備",
    # C-3 供應商履約
    "人員服務", "駕駛接送", "帶團節奏", "約定履行", "現場安全與衛生", "風險應變與告知", "不當行為",
    # C-4 平台與系統
    "開通啟用", "憑證與資格", "平台功能",
    # C-5 客服營運
    "確認/修改", "取消/退款", "客服應對",
    # C-6 理解期待
    "個人因素", "價值感落差", "內容期待落差", "天候與自然因素", "外部突發事件", "資訊誤讀",
]


def load_rows(csv_path: Path) -> list[tuple[str, int]]:
    rows: list[tuple[str, int]] = []
    with csv_path.open(newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            try:
                rows.append((row["tag_name"].strip(), int(row["tag_cnt"])))
            except (KeyError, ValueError):
                continue
    return rows


def classify(tag: str, mapping: dict) -> str:
    """回傳 mapped / non_attr / fallback / uncovered。"""
    entry = mapping.get(tag)
    if entry is not None:
        return "mapped" if (entry.get("l1") or entry.get("l2")) else "non_attr"
    if any((tag in label) or (label in tag) for label in L2_LABELS):
        return "fallback"
    return "uncovered"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True, help="free tag 匯總表 CSV 路徑")
    parser.add_argument("--top", type=int, default=30, help="列出未覆蓋 TopN")
    parser.add_argument("--min-pass", type=float, default=0.95, help="驗收線（加權處理覆蓋率）")
    args = parser.parse_args()

    csv_path = Path(args.csv).expanduser()
    if not csv_path.exists():
        print(f"CSV 不存在：{csv_path}", file=sys.stderr)
        return 2

    mapping = json.loads(MAPPING_PATH.read_text(encoding="utf-8"))["mapping"]
    rows = load_rows(csv_path)
    total = sum(cnt for _, cnt in rows)

    buckets = {"mapped": 0, "non_attr": 0, "fallback": 0, "uncovered": 0}
    uncovered: list[tuple[str, int]] = []
    for tag, cnt in rows:
        kind = classify(tag, mapping)
        buckets[kind] += cnt
        if kind == "uncovered":
            uncovered.append((tag, cnt))

    handled = total - buckets["uncovered"]
    mapped_only = buckets["mapped"] + buckets["fallback"]
    print(f"總量 {total:,}（{len(rows):,} tags）｜mapping 條目 {len(mapping)}")
    print(f"加權處理覆蓋率: {handled / total:.1%}（顯式 {buckets['mapped'] / total:.1%} + 兜底 {buckets['fallback'] / total:.1%} + 顯式無對應 {buckets['non_attr'] / total:.1%}）")
    print(f"純歸因映射覆蓋率: {mapped_only / total:.1%}")

    uncovered.sort(key=lambda x: -x[1])
    print(f"未覆蓋 Top{args.top}:")
    for tag, cnt in uncovered[: args.top]:
        print(f"  {tag}  {cnt:,}")

    ok = handled / total >= args.min_pass
    print(f"驗收（≥{args.min_pass:.0%}）: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

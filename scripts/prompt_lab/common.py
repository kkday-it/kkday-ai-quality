"""Prompt Lab 共用小工具——JSONL 讀寫（原子）、SHA-256、確定性 case_id 槽位、成本護欄。

純函式、零 API、零 backend 依賴。供 generate/audit/build_dataset/evaluate/report 共用。
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel


def read_jsonl(path: str | Path) -> list[dict]:
    """讀 JSONL 為 dict list（檔案不存在回空清單；跳過空行）。"""
    p = Path(path)
    if not p.exists():
        return []
    out: list[dict] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def write_jsonl(path: str | Path, rows: list[Any]) -> None:
    """原子寫 JSONL（先寫 .tmp 再 rename）；rows 可為 dict 或 pydantic BaseModel。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for r in rows:
            obj = r.model_dump() if isinstance(r, BaseModel) else r
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")
    os.replace(tmp, p)


def sha256_text(s: str) -> str:
    """字串 UTF-8 位元組的 SHA-256 hex。"""
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def sha256_file(path: str | Path) -> str:
    """檔案位元組的 SHA-256 hex。"""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def slot_case_ids(cell_id: str, case_family: str, target_count: int) -> list[str]:
    """一個 plan cell 的確定性 case_id 槽位（resume 用：據此判斷該格是否已產齊）。

    contrast_pair：cell_id-a（true 側）/ cell_id-b（false 側）。其餘：cell_id-01…-NN。
    """
    if case_family == "contrast_pair":
        return [f"{cell_id}-a", f"{cell_id}-b"]
    return [f"{cell_id}-{i:02d}" for i in range(1, target_count + 1)]


def confirm_cost_or_exit(
    n_calls: int, *, all_flag: bool, confirm_cost: bool, limit: int
) -> int:
    """成本護欄（PRD §15）：回傳實際允許的呼叫數；違規則印訊息並 sys.exit(2)。

    - 預設上限 limit（=5）；要跑超過須 --all。
    - --all 全量真打須 --confirm-cost。
    """
    if n_calls <= limit:
        return n_calls
    if not all_flag:
        print(
            f"⚠️  待處理 {n_calls} 次呼叫 > 預設上限 {limit}。僅處理前 {limit} 個；"
            f"要全量請加 --all（全量真打另需 --confirm-cost）。",
            file=sys.stderr,
        )
        return limit
    if not confirm_cost:
        print(
            f"⛔ --all 全量（{n_calls} 次呼叫）需顯式 --confirm-cost 以確認成本。中止。",
            file=sys.stderr,
        )
        sys.exit(2)
    return n_calls

#!/usr/bin/env python3
"""C-4 使用兌換體驗重組後，把 judgments 既有舊 code re-key 成新 3 段 code。

C-4 由 8 個 L2 葉重組為 3 面向 × 8 L3（見 config/ai_judge/rule_C-4.json）。既有 judgments.data
JSON 內若殘留舊 code（C-4-1~C-4-8）需一併換成新 code，否則會變孤兒（rule 已無舊 code）。

映射（1:1，內容不變只換編碼）：
    C-4-1 憑證核銷 → C-4-2-1      C-4-2 啟用設定 → C-4-1-1
    C-4-3 效期時機 → C-4-1-2      C-4-4 取票/入場 → C-4-2-2
    C-4-5 預約時段 → C-4-3-1      C-4-6 使用限制 → C-4-3-2
    C-4-7 數量份數 → C-4-3-3      C-4-8 出示操作 → C-4-2-3

以單次 regex 取代（只配舊 2 段 code、後面不接 -數字），一併換 data JSON 內所有欄位（l3_code /
l2_code / l3_candidates 等）。sqlalchemy 為重庫、於函式內 lazy import。

用法（於後端環境）：
    python scripts/rekey_c4_codes.py            # dry-run，只報數不改
    python scripts/rekey_c4_codes.py --apply     # 實際寫回 judgments.data
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# 舊 2 段 code → 新 3 段 code（順序不可依賴 dict，靠 regex 單次配對避免鏈式覆蓋）
_REMAP = {
    "C-4-1": "C-4-2-1",
    "C-4-2": "C-4-1-1",
    "C-4-3": "C-4-1-2",
    "C-4-4": "C-4-2-2",
    "C-4-5": "C-4-3-1",
    "C-4-6": "C-4-3-2",
    "C-4-7": "C-4-3-3",
    "C-4-8": "C-4-2-3",
}
# 只配舊 2 段 code（後面不接 -數字），避免「先換 C-4-1 產生 C-4-2-1 再被 C-4-2 二次改」的鏈式 bug
_PAT = re.compile(r"C-4-[1-8](?![-\d])")


def _remap_text(s: str) -> str:
    """把字串內所有舊 C-4-N code 單次映射為新 code。"""
    return _PAT.sub(lambda m: _REMAP[m.group(0)], s)


def main() -> int:
    """掃 judgments.data，含舊 C-4-N code 者 re-key；--apply 才寫回。"""
    apply = "--apply" in sys.argv
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
    try:
        from app.core import tables as T
        from sqlalchemy import select, update
    except ImportError as exc:
        print(f"需在後端環境執行（缺 {exc.name}）", file=sys.stderr)
        return 1

    engine = T.get_engine()
    touched = 0
    with engine.begin() as c:
        rows = list(c.execute(select(T.judgments.c.id, T.judgments.c.data)))
        for jid, raw in rows:
            if not raw or "C-4-" not in raw:
                continue
            new = _remap_text(raw)
            if new == raw:
                continue
            touched += 1
            if apply:
                c.execute(update(T.judgments).where(T.judgments.c.id == jid).values(data=new))
    verb = "已 re-key" if apply else "待 re-key（dry-run）"
    print(f"judgments 總列 {len(rows)}｜{verb} {touched} 列含舊 C-4-N code")
    if not apply and touched:
        print("→ 確認無誤後加 --apply 實際寫回")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

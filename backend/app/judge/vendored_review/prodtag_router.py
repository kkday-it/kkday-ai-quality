"""prod_tag 類目路由（重整自 ai_review_system/backend/review_prodtag.py · Rule4 oid=90）。

依商品 `prod_tags`=[main, sub…] 路由到對應的 T3 類目葉子 prompt（category_prompts/）：
- 有可用 sub（有葉子檔）→ 以 sub 為主（去重保序）
- 無 sub、main 為 T3 → [main]；main 為 T2 → 展開為其所有有葉子檔的 T3 子分類

AI 法官用途：adequacy 判準的「類目敏感」疊加層——同一欄位在不同商品類目（美食之旅 vs 跳傘 vs Spa）
判準不同。只依賴 stdlib，讀本目錄 rule4_prodtag_defs.json（分類樹）+ category_prompts/（葉子）。
重整自上游：路徑改指 vendored_review；其餘邏輯逐字沿用（cache/T2展開/退回 main 等）。
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

PRODTAG_RULE_OID = 90

_HERE = Path(__file__).resolve().parent
_DEFS_PATH = _HERE / "rule4_prodtag_defs.json"
_LEAVES_DIR = _HERE / "category_prompts"

_lock = threading.Lock()
_codes_cache: dict | None = None
_children_cache: dict[str, list[str]] | None = None
_leaf_index: dict[str, Path] | None = None


def _codes() -> dict:
    """載入並快取分類定義表（codes 區塊）：查 tier / parent 用。檔案缺失 → 空 dict。"""
    global _codes_cache
    with _lock:
        if _codes_cache is None:
            try:
                _codes_cache = json.loads(_DEFS_PATH.read_text(encoding="utf-8")).get("codes", {})
            except (OSError, ValueError):
                _codes_cache = {}
        return _codes_cache


def _children() -> dict[str, list[str]]:
    """父碼 → 直接子碼清單（用於把 T2 展開成其 T3 子分類）。"""
    global _children_cache
    codes = _codes()  # 先取（會自行上鎖）——勿在持有 _lock 時呼叫，否則 Lock 不可重入會死鎖
    with _lock:
        if _children_cache is None:
            idx: dict[str, list[str]] = {}
            for code, rec in codes.items():
                parent = rec.get("parent")
                if parent:
                    idx.setdefault(parent, []).append(code)
            _children_cache = idx
        return _children_cache


def _leaf_files() -> dict[str, Path]:
    """掃 category_prompts/，建 CATEGORY_xxx → .md 路徑索引（檔名 `<code>__名稱.md`，僅 T3）。"""
    global _leaf_index
    with _lock:
        if _leaf_index is None:
            idx: dict[str, Path] = {}
            if _LEAVES_DIR.is_dir():
                for f in _LEAVES_DIR.glob("CATEGORY_*.md"):
                    idx.setdefault(f.name.split("__", 1)[0].split(".", 1)[0], f)
            _leaf_index = idx
        return _leaf_index


def routing_codes(prod_tags: list[str]) -> list[str]:
    """把商品分類解析成「要用哪些 T3 分類的 prompt」（只回有對應葉子檔的碼）。

    sub 全部不在範圍時自動退回 main，不致漏判在範圍的 main。
    """
    if not prod_tags:
        return []
    leaves = _leaf_files()
    subs = [s for s in dict.fromkeys(prod_tags[1:]) if s in leaves]
    if subs:
        return subs
    main = prod_tags[0]
    node = _codes().get(main)
    if node is None:
        return []
    tier = node.get("tier")
    if tier == 3:
        return [main] if main in leaves else []
    if tier == 2:
        return [c for c in _children().get(main, []) if c in leaves]  # T2 → 旗下有葉子檔的 T3
    return []  # T1 或未知（商品 main 不會是 T1）


def _leaf_content(code: str) -> str | None:
    fp = _leaf_files().get(code)
    if fp is None:
        return None
    try:
        text = fp.read_text(encoding="utf-8")
    except OSError:
        return None
    return text if text.strip() else None


def routed_prompt_blocks(prod_tags: list[str]) -> list[str]:
    """路由出的各 T3 分類葉子 prompt 內容清單（查無葉子檔的碼略過）。

    回 1 份 → 直接用；≥2 份 → 呼叫端組裝（符合任一即放行）；[] → 不在範圍。
    """
    out: list[str] = []
    for code in routing_codes(prod_tags):
        text = _leaf_content(code)
        if text:
            out.append(text)
    return out


def routed_prompt_files(prod_tags: list[str]) -> list[str]:
    """路由出的各分類對應葉子檔名（如 `CATEGORY_028__美食之旅.md`），供顯示用了哪些 prompt。"""
    idx = _leaf_files()
    return [idx[c].name for c in routing_codes(prod_tags) if c in idx]


def has_prompt(prod_tags: list[str]) -> bool:
    """商品是否在類目範圍內＝路由出至少一個分類（供 gate 判斷）。"""
    return bool(routing_codes(prod_tags))


def reset_cache() -> None:
    """測試用：清快取，下次重讀檔 / 重掃葉子目錄。"""
    global _codes_cache, _children_cache, _leaf_index
    with _lock:
        _codes_cache = None
        _children_cache = None
        _leaf_index = None

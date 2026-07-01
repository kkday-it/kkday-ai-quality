"""反饋來源欄位映射（config/ai_judge/source_mapping.json 驅動）。

5 來源（conversations/freshdesk_tickets/product_reviews/app_feedback/mixpanel_tracker）原始表頭
天差地別；本模組以**外部化 SSOT** 提供三件事，供上傳流程與正規化共用（零硬編碼別名）：
  - detect_source(headers)：依 required_headers 指紋自動辨識上傳工作表屬哪個來源。
  - validate_headers(source, headers)：校驗必備欄是否齊全（缺則不可上傳）。
  - normalize_row(source, row)：原始列 → 統一問題列表 canonical 欄 + source_metadata(特殊欄)。

公共欄位共用、特殊欄入 source_metadata、source 欄即「反饋管道」——對齊統一問題列表設計。
快取：首次存取 lazy 載入；config 線上編輯後呼叫 reload()。
"""

from __future__ import annotations

import json
from typing import Any

from app.core.paths import AI_JUDGE_DIR  # config/ai_judge 目錄（統一定位）

_MAPPING_FILE = AI_JUDGE_DIR / "source_mapping.json"

_sources: dict[str, dict[str, Any]] = {}
_canonical: list[str] = []
_loaded = False


def _ensure_loaded() -> None:
    """lazy 載入 source_mapping.json（冪等）。"""
    global _loaded
    if _loaded:
        return
    data = json.loads(_MAPPING_FILE.read_text(encoding="utf-8"))
    _sources.clear()
    _sources.update(data.get("sources", {}))
    _canonical.clear()
    _canonical.extend(data.get("_meta", {}).get("canonical_fields", []))
    _loaded = True


def reload() -> None:
    """清快取重載（config 編輯後使映射即時生效）。"""
    global _loaded
    _loaded = False
    _ensure_loaded()


def sources() -> dict[str, dict[str, Any]]:
    """全部來源映射定義（key=source code）。"""
    _ensure_loaded()
    return _sources


def canonical_fields() -> list[str]:
    """統一問題列表的公共欄位清單。"""
    _ensure_loaded()
    return list(_canonical)


def source_label(source: str) -> str:
    """來源 code → 中文顯示名；委派 sources.label_for（label SSOT 唯一在 config/global/sources.json）。

    本檔（欄位映射）不再自帶 label，避免與 sources.json 兩份漂移。
    """
    from app.core import sources

    return sources.label_for(source)


def _norm_headers(headers: list[str]) -> set[str]:
    """表頭正規化集合（去空白、濾空）供指紋比對。"""
    return {str(h).strip() for h in headers if h is not None and str(h).strip()}


def detect_source(headers: list[str]) -> str | None:
    """依 required_headers 指紋辨識表頭屬哪個來源；辨識不出回 None。

    各來源 required_headers 互斥（用各自獨有欄），正常僅一個全中；
    若多個命中（理論上不會），取 required 命中數最多者（最具體）。

    Args:
        headers: 上傳工作表的表頭清單。

    Returns:
        命中的 source code；無任何來源 required 全齊回 None。
    """
    _ensure_loaded()
    hset = _norm_headers(headers)
    best: tuple[int, str | None] = (0, None)
    for code, m in _sources.items():
        req = m.get("required_headers", [])
        if req and all(h in hset for h in req) and len(req) > best[0]:
            best = (len(req), code)
    return best[1]


def validate_headers(source: str, headers: list[str]) -> list[str]:
    """回傳該來源缺少的必備表頭（空 list＝通過）。

    Args:
        source: 來源 code。
        headers: 工作表表頭清單。

    Returns:
        缺少的 required_headers；source 未知時回 ["<unknown source>"]。
    """
    _ensure_loaded()
    m = _sources.get(source)
    if not m:
        return ["<unknown source>"]
    hset = _norm_headers(headers)
    return [h for h in m.get("required_headers", []) if h not in hset]


def _clean(v: Any) -> Any:
    """空字串/None/'nan'/'null' → None；其餘 strip 後原樣。"""
    if v is None:
        return None
    s = str(v).strip()
    if s == "" or s.lower() in {"nan", "null", "none"}:
        return None
    return s


def normalize_row(source: str, row: dict[str, Any]) -> dict[str, Any]:
    """原始列 → 統一問題列表 canonical 欄 + source_metadata（特殊欄兜底）。

    映射規則（讀 config）：field_map 把原始欄映到 canonical；const_fields 補常數；
    field_map 未涵蓋的非空原始欄一律進 source_metadata。source 欄填來源 code（反饋管道）。

    Args:
        source: 來源 code（須在 source_mapping 內）。
        row: 原始一列（header→value）。

    Returns:
        dict：canonical 欄（含 source）+ source_metadata(dict)。值經 _clean 正規化。
    """
    _ensure_loaded()
    m = _sources.get(source) or {}
    field_map: dict[str, str] = m.get("field_map", {})
    const_fields: dict[str, str] = m.get("const_fields", {})

    out: dict[str, Any] = {"source": source}
    meta: dict[str, Any] = {}
    for raw_key, value in row.items():
        if raw_key is None:
            continue
        key = str(raw_key).strip()
        canon = field_map.get(key)
        cv = _clean(value)
        if canon:
            out[canon] = cv
        elif cv is not None:
            meta[key] = cv  # 特殊欄兜底
    for k, v in const_fields.items():
        out.setdefault(k, v)
    out["source_metadata"] = meta
    return out

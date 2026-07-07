"""反饋來源目錄（config/global/sources.json 驅動，前後端共用 SSOT）。

label（顯示名）與 natural_key（增量上傳 upsert 的自然唯一鍵）的單一真相源。
前端以 @config/global/sources.json 同讀同一份；後端欄位映射/偵測指紋另在
config/ai_judge/source_mapping.json（後端專用，以相同 source value 對應，不重複 label）。
集中此處供 _enrich_problem 的 source_label、per-source table registry 的 natural_key、
上傳偵測共用，避免各處各寫一份 label 而 drift。
"""

from __future__ import annotations

import json

from app.core.paths import GLOBAL_DIR as _GLOBAL_DIR

# 模組載入時讀一次（非機密、變動極少；改檔需重啟後端，與其他 config/global 一致）。
_SOURCES: list[dict] = json.loads((_GLOBAL_DIR / "sources.json").read_text(encoding="utf-8")).get(
    "sources", []
)

_BY_VALUE: dict[str, dict] = {s["value"]: s for s in _SOURCES}


def all_sources() -> list[dict]:
    """回傳所有反饋來源定義（{value, label, hint, natural_key}），順序同 config。"""
    return list(_SOURCES)


def label_for(source: str) -> str:
    """來源 code → 中文顯示 label；未知 code 回傳原 code（不拋錯，供顯示層安全回退）。

    Args:
        source: 來源 code（如 'product_reviews'）。

    Returns:
        中文 label（如 '商品評論'）；查無則原樣回傳 source。
    """
    hit = _BY_VALUE.get(source)
    return hit["label"] if hit else source


def natural_key(source: str) -> str | None:
    """來源 code → 增量上傳 upsert 的自然唯一鍵欄名（如 product_reviews → 'rec_oid'）。

    Args:
        source: 來源 code。

    Returns:
        自然鍵欄名；未知來源回傳 None（呼叫端須自行處理無自然鍵情境）。
    """
    hit = _BY_VALUE.get(source)
    return hit.get("natural_key") if hit else None

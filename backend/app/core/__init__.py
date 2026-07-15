"""core barrel：judge_config/ 判準 config loader 根層 re-export。

6 個判準領域 config loader（讀 config/ai_judge、config/global JSON）歸組於 judge_config/，
此處 re-export 至 core 根層，使既有 `from app.core import ai_judge` 等消費端零改動。
"""

from app.core.judge_config import (
    ai_judge,
    pricing,
    product_vertical,
    rule_export,
    source_mapping,
    sources,
)

__all__ = [
    "ai_judge",
    "pricing",
    "product_vertical",
    "rule_export",
    "source_mapping",
    "sources",
]

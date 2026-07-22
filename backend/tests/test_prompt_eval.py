"""prompt_eval.py 對照測試（純函式指標測試見 test_eval_prompt_metrics.py）。

`prompt_id_of` 鎖死 C-N ↔ prompt_id 對照（樹退役後改自 `prompt_source` 檔名尾綴派生，非 DB 樹
tree[0].domain——回歸測試防再次踩壞）。
"""

from __future__ import annotations

import pytest

from app.judge import prompt_eval


# ─────────────────────────── prompt 對照 ───────────────────────────
def test_prompt_id_of_maps_polarity_and_domains() -> None:
    """polarity → POLARITY_ID；C-N → 對應 prompt_id；未知拋 ValueError。"""
    assert prompt_eval.prompt_id_of("polarity") == "00_polarity"
    assert prompt_eval.prompt_id_of("C-3") == "03_C-3_supplier"
    with pytest.raises(ValueError, match="未知 prompt"):
        prompt_eval.prompt_id_of("C-9")

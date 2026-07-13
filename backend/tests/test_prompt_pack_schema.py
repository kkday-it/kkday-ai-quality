"""評測 Prompt 包 schema 鏡射護欄。

gen_eval_prompt_pack._output_schema 是 prejudge._attr_schema_l2_multi 的鏡射（生成器刻意不 import
production 私有函式、保持唯讀輕依賴）——兩份平行代碼靠本測試逐鍵比對鎖死：任一側改動 schema
形狀而未同步另一側，測試即紅。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from app.judge.prejudge import _attr_schema_l2_multi

_GEN_PATH = Path(__file__).resolve().parents[2] / "scripts" / "tools" / "gen_eval_prompt_pack.py"


def _load_generator():
    """以檔案路徑載入生成器模組（scripts/ 非 package，不入 sys.path 汙染其他測試）。"""
    spec = importlib.util.spec_from_file_location("gen_eval_prompt_pack", _GEN_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_output_schema_mirrors_production():
    """生成器單域 schema 必須與 production L2 多歸因 schema 逐鍵相等（enum 排序含在內）。"""
    gen = _load_generator()
    codes = ["C-3-4", "C-3-1", "C-3-7"]
    for max_n in (1, 2, 3):
        assert gen._output_schema(codes, max_n) == _attr_schema_l2_multi(frozenset(codes), max_n), (
            f"schema 鏡射漂移（max_n={max_n}）：同步 gen_eval_prompt_pack._output_schema 與 prejudge._attr_schema_l2_multi"
        )

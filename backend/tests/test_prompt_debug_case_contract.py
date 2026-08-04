"""人工評判案例請求 model（`PromptDebugCaseIn`）的契約不變式。

案例於 2026-08-04 改存前端本地（Pinia + localStorage），`prompt_debug_reviews` 表與其 CRUD 端點
一併退場。原本掛在 `POST /prompt-debug/reviews` 上的兩條驗證，隨之搬到請求 model——
**這裡是案例進入後端的唯一入口**，前端 store 若被改壞（改版、手動編輯 localStorage），
沒有這道驗證就會把錯誤資料直接餵進回歸計分。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.api.routers.v1.prompt_debug import PromptDebugCaseIn
from app.judge import prompt_debug

_VALID_KEY = prompt_debug.OUTPUT_FIELDS[0]["key"]


def _case(**kw) -> dict:
    return {"id": 1, "conversation": "[USER] 測試", "ai_output": {}, **kw}


def test_accepts_contract_fields() -> None:
    """corrections / confirmed 用契約內欄名 → 通過。"""
    c = PromptDebugCaseIn(**_case(corrections={_VALID_KEY: "正解"}, confirmed=[]))
    assert c.corrections == {_VALID_KEY: "正解"}


def test_rejects_unknown_correction_field() -> None:
    """不認得的欄名 → 擋下（無從比對，放進去只會讓回歸計分失真）。"""
    with pytest.raises(ValidationError, match="不認得的欄位"):
        PromptDebugCaseIn(**_case(corrections={"不存在的欄": "x"}))


def test_rejects_unknown_confirmed_field() -> None:
    """confirmed 也走同一套欄名檢查。"""
    with pytest.raises(ValidationError, match="不認得的欄位"):
        PromptDebugCaseIn(**_case(confirmed=["不存在的欄"]))


def test_rejects_field_marked_both_right_and_wrong() -> None:
    """同一欄不能既標對又標錯——語義自相矛盾，回歸時無從判定該欄是否算分。"""
    with pytest.raises(ValidationError, match="既標對又標錯"):
        PromptDebugCaseIn(**_case(corrections={_VALID_KEY: "正解"}, confirmed=[_VALID_KEY]))


def test_empty_corrections_is_valid_positive_case() -> None:
    """全欄皆對（corrections={}）是合法的正例——回歸時用來防過度矯正。"""
    c = PromptDebugCaseIn(**_case(corrections={}, confirmed=[_VALID_KEY]))
    assert c.corrections == {}

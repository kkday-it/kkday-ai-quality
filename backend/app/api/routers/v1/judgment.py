"""判決端點（第三階段實作；第一階段接口預留，回 501）。

設計：單條 = 批量的特例（judge_one = judge_batch([id])）；支援按條件篩選批量判決。
判決 = interaction（問題）× product（商品正確內容）校驗 → 分類 → 分析 → action → finding。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/judgment", tags=["judgment"])


class JudgeIn(BaseModel):
    """判決請求：interaction_ids（單條/批量）或 篩選條件（整體批量）。"""

    interaction_ids: list[str] | None = None
    prod_oid: str | None = None
    source: str | None = None
    verdict: str | None = None
    status: str | None = None


@router.post("/run")
def run_judgment(body: JudgeIn) -> dict:
    """執行判決（單條 / 批量 / 按條件批量）。第三階段實作。"""
    raise HTTPException(status_code=501, detail="判決層為第三階段，尚未實作（接口已預留）")

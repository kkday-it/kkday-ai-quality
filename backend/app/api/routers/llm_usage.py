"""AI 使用紀錄端點（消耗 dashboard 聚合）；全路徑自帶 /api。用量寫入由初判管線內部處理，此處僅讀。"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core import auth, db

router = APIRouter()


@router.get("/api/llm-usage/overview")
def get_llm_usage_overview(
    date_from: str | None = None,
    date_to: str | None = None,
    granularity: str = "day",
    _: dict = Depends(auth.get_current_user),
) -> dict:
    """AI 消耗聚合：KPI（總成本/總 token/總呼叫/快取）+ 趨勢 + 各模型/階段/來源分布。

    可選 date_from/date_to（'YYYY-MM-DD' 區間，含端點）與 granularity（year|month|day，趨勢粒度）。
    """
    return db.llm_usage_overview(date_from=date_from, date_to=date_to, granularity=granularity)

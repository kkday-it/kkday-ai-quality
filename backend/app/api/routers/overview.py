"""質檢概覽（overview 首頁）真實指標端點；全路徑自帶 /api。

「縮窄真接」範圍：僅 AI 法官可自 DB 聚合的指標（judgments 內容類占比/歸因樣本）；
審品達標率 / CVR / 售後進線等外部系統指標（Google Sheet / Tableau / Looker）不在此，
前端維持 config 驅動示意值並標註「外部資料」。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core import auth, db

router = APIRouter()


@router.get("/api/overview/ai-judge")
def get_ai_judge_overview(months: int = 6, _user: dict = Depends(auth.get_current_user)) -> dict:
    """AI 法官真實指標：內容類占比月趨勢（judged_at 軸·distinct 進線）+ 總量。

    供 overview 首頁 DashboardView 覆蓋 mock 的 ai_judge 區塊（引擎卡 / 北極星
    intake_content_ratio / laggingTrend）；口徑見 db.ai_judge_overview_stats docstring。
    """
    return db.ai_judge_overview_stats(months=months)

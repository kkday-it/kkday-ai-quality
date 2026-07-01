"""v1 API：聚合所有 v1 端點於 /api/v1 前綴下。

新攝取架構（PostgreSQL）的端點集中在此；舊 sqlite 端點留在 main.py，待第三階段汰換。
"""

from fastapi import APIRouter

from app.api.routers.v1 import (
    export,
    judgment,
)

# TODO(in-progress)：ingest / interactions / products / signals 模組檔尚未建立，
# 先註解避免 import 失敗使整個 app 無法啟動；對應 .py 建好後再取消註解。
# from app.api.routers.v1 import ingest, interactions, products, signals

router = APIRouter(prefix="/api/v1")
# router.include_router(ingest.router)
# router.include_router(interactions.router)
# router.include_router(products.router)
# router.include_router(signals.router)
router.include_router(export.router)
router.include_router(judgment.router)

__all__ = ["router"]

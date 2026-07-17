"""v1 API：聚合 v1 端點於 /api/v1 前綴下。"""

from fastapi import APIRouter

from app.api.routers.v1 import prejudge

router = APIRouter(prefix="/api/v1")
router.include_router(prejudge.router)

__all__ = ["router"]

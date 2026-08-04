"""v1 API：聚合 v1 端點於 /api/v1 前綴下。

prejudge 與 prompt_debug 兩個 router 皆用 prefix="/prejudge"，實際路徑為
/api/v1/prejudge/... 與 /api/v1/prejudge/prompt-debug/...。
"""

from fastapi import APIRouter

from app.api.routers.v1 import prejudge, prompt_debug

router = APIRouter(prefix="/api/v1")
router.include_router(prejudge.router)
router.include_router(prompt_debug.router)

__all__ = ["router"]

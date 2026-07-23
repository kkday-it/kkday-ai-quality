"""v1 API：聚合 v1 端點於 /api/v1 前綴下。

prejudge/prompt_debug/prompt_sandbox 三個 router 皆用 prefix="/prejudge"（2026-07-23 從單一
v1/prejudge.py 拆分而來，對應原本混在一起的三個端點群組）——路徑與 A/前一版完全一致
（/api/v1/prejudge/...、/api/v1/prejudge/prompt-debug/...、/api/v1/prejudge/prompt-sandbox/...），
前端零改動。
"""

from fastapi import APIRouter

from app.api.routers.v1 import prejudge, prompt_debug, prompt_sandbox

router = APIRouter(prefix="/api/v1")
router.include_router(prejudge.router)
router.include_router(prompt_debug.router)
router.include_router(prompt_sandbox.router)

__all__ = ["router"]

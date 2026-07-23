"""售後根因 ad-hoc Prompt 調試端點：任意文字 → 即時結構化裁決（SSE，不落 attributions）。

自 v1/prejudge.py 拆出（2026-07-23，原檔混三領域違反一 router 一領域慣例）；
LlmOverridesIn 重用 prejudge.py 的共用契約，不另立第三個共用模組。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.core import auth
from app.core import settings as app_settings
from app.core.permissions import permission_keys, require_permission

from .prejudge import LlmOverridesIn

router = APIRouter(prefix="/prejudge", tags=["prompt-debug"])


class PromptDebugIn(BaseModel):
    """任意售後對話 Prompt 調試請求。"""

    text: str = Field(min_length=1, max_length=200_000)
    system_prompt: str = Field(min_length=1, max_length=300_000)
    overrides: LlmOverridesIn | None = None  # 本次臨時旋鈕覆寫；缺省沿用 prompt_debug 功能區默認


@router.get("/prompt-debug/defaults")
def prompt_debug_defaults(user: dict = Depends(auth.get_current_user)) -> dict:
    """回傳 Google Doc 分類庫渲染的預設 Prompt、schema 與裁判表摘要。"""
    from app.judge import prompt_debug

    return prompt_debug.defaults_payload()


@router.post("/prompt-debug/stream")
def prompt_debug_stream(
    body: PromptDebugIn,
    user: dict = Depends(require_permission(permission_keys.PREJUDGE_RUN)),
) -> StreamingResponse:
    """以 SSE 串流任意文字的結構化裁決、欄位校驗與本次 token/費用。"""
    from app.judge import prompt_debug

    saved = app_settings.load_settings()
    overrides = body.overrides.model_dump(exclude_unset=True) if body.overrides else None
    effective = app_settings.effective_llm_dict(saved, area="prompt_debug", overrides=overrides)
    if not app_settings.resolve_provider_token(effective):
        raise HTTPException(
            status_code=400,
            detail="目前配置沒有可用 API token，請先在「配置 › LLM 模型連線」完成設定",
        )
    if not (effective.get("model") or "").strip():
        raise HTTPException(status_code=400, detail="本次調試未指定 model")

    return StreamingResponse(
        prompt_debug.stream_frames(body.text, body.system_prompt, effective),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

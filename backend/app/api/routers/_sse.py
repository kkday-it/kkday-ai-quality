"""job 進度 SSE 串流的共用產生器。

四個 router（exports / inbound / admin_import / v1.prejudge）原本各自手寫同一段
「輪詢快照 → yield SSE event → 終態即結束」的迴圈與同一組 header。抽出來的直接理由不是
去重，而是**其中三份的終態判定是錯的**：

    exports      export_jobs.is_terminal()        ✅ 四種終態全涵蓋
    inbound      ("done", "error")                ❌ 漏 cancelled / interrupted
    admin_import ("done", "error")                ❌ 同上
    v1.prejudge  ("done", "error", "cancelled")   ❌ 漏 interrupted

`shutdown.mark_running_jobs_interrupted()` 會在服務收尾時把進行中的 job 設為
``interrupted``，而 import_jobs 與 upload_batch 都有 `mark_running_interrupted()`——
於是那三條串流會**永遠不結束**，前端對一個再也不會變的 job 無限輪詢，且沒有任何錯誤浮現。
終態判定收斂到 `job_registry.is_terminal()` 這個 SSOT 後，加新終態不必再逐處補。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable

from fastapi.responses import StreamingResponse

from app.core.job_registry import is_terminal

# nginx / ALB 會緩衝 text/event-stream，不關掉就變成「跑完才一次吐出」。
# 對外公開：語義不同、無法共用本模組迴圈的 SSE 端點（日誌增量游標、LLM 串流輸出）仍該共用同一組 header。
SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def job_progress_stream(
    get_job: Callable[[], dict | None], *, interval: float = 0.5
) -> StreamingResponse:
    """把 job 快照輪詢成 SSE 串流。

    Args:
        get_job: 取當前快照的無參數 callable（回 None＝job 不存在）。
        interval: 輪詢間隔秒數。預設 0.5；長時任務可放寬以減少空轉。

    Returns:
        `text/event-stream` 的 StreamingResponse；job 不存在推一則 `event: error` 後結束，
        終態則推完最後一筆快照再結束（前端拿得到最終狀態，不必另外查一次）。
    """

    async def _events():
        while True:
            snap = get_job()
            if snap is None:
                yield f"event: error\ndata: {json.dumps({'detail': 'job 不存在'}, ensure_ascii=False)}\n\n"
                return
            yield f"data: {json.dumps(snap, ensure_ascii=False)}\n\n"
            if is_terminal(snap["status"]):
                return
            await asyncio.sleep(interval)

    return StreamingResponse(_events(), media_type="text/event-stream", headers=SSE_HEADERS)

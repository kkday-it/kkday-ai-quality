"""graceful shutdown 收尾：彙總標記 4 套 in-mem job registry 的進行中 job 為 interrupted。

main.py 的 lifespan shutdown 段唯一呼叫點（main 只組裝、邏輯收斂於此）。SIGTERM 後
uvicorn drain in-flight 請求期間，輪詢進度的前端可拿到明確終態；daemon thread 本身
隨 process 消逝無法回收——單 worker in-mem registry 的既定限制（P1 遷 Redis/PG 後解）。
"""

from __future__ import annotations

import logging

_log = logging.getLogger(__name__)


def mark_running_jobs_interrupted() -> dict[str, list[str]]:
    """把 4 套 registry（export/import/prejudge/upload）仍在跑的 job 全標 interrupted。

    函式內 import：shutdown 僅在 process 結束時走一次，不讓 core 底層模組
    在頂層反向依賴 judge 套件（維持依賴方向單向）。

    Returns:
        registry 名 → 被標記的 job_id 清單（全空＝無進行中 job）。
    """
    from app.core import export_jobs, import_jobs
    from app.judge import prejudge_batch
    from app.judge.ingest import upload_batch

    marked = {
        "export": export_jobs.mark_running_interrupted(),
        "import": import_jobs.mark_running_interrupted(),
        "prejudge": prejudge_batch.mark_running_interrupted(),
        "upload": upload_batch.mark_running_interrupted(),
    }
    hit = {k: v for k, v in marked.items() if v}
    if hit:
        _log.warning("graceful shutdown：進行中 job 標記 interrupted：%s", hit)
    return hit

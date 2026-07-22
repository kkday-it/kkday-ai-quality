"""訂單佐證唯讀查詢端點（詳情抽屜 lazy fetch）；全路徑自帶 /api。

與判決管線共用 `qc_evidence.get_evidence()`（同一套快取/single-flight/熔斷/PII 防線），
但憑證走「當前登入 user」自己的解析（env 服務帳號優先 → 該 user 的 production QC 連線）——
與 /api/v1/prejudge 批次的觸發者憑證是各自獨立的注入路徑。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core import auth
from app.core import settings as app_settings
from app.core.db import qc_evidence

router = APIRouter()


@router.get("/api/evidence/{order_oid}")
def get_order_evidence(order_oid: str, user: dict = Depends(auth.get_current_user)) -> dict:
    """單筆訂單佐證（下單當時商品快照投影）。

    Returns:
        {status, data}；status 見 qc_evidence.EvidenceResult（fetched/cache_hit/
        no_order_oid/not_found/degraded_unavailable/error），非成功時 data=None——
        前端據此顯示三態，不拋 5xx（佐證缺失是常態不是錯誤）。
    """
    # 系統級憑證（env 服務帳號 → 當前 user → 全庫任一 production）：佐證是團隊共享唯讀
    # 快照，不綁「當前登入者是否自己配過連線」（否則僅配過者查得到，其他人全降級）
    s = app_settings.load_settings()
    qc_evidence.set_current(qc_evidence.resolve_credentials_any(s))
    result = qc_evidence.get_evidence(order_oid)
    return {"status": result.status, "data": result.data}

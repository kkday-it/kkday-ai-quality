"""資料存取層（純查詢，不含業務邏輯）。寫入走 ingestion/pipeline.py。"""

from app.repositories import interaction_repo, product_repo, signal_repo, sync_repo

__all__ = ["interaction_repo", "product_repo", "signal_repo", "sync_repo"]

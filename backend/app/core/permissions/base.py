"""權限抽象介面（Protocol）：所有 provider 的共同契約。

刻意設計成 be2 中央 Auth SVC 委派制的形狀——現行 local provider 由 config 白名單派生權限，
日後換 be2 provider（打 verify API）呼叫端零改動。**fail-closed 為介面契約**：provider 內部
出錯應由 `require_permission` 收斂為 403，不得 fail-open（見 deps.py）。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class PermissionProvider(Protocol):
    """權限來源抽象：把「使用者 → 具備的 business-key 權限集合」與「單點檢查」標準化。"""

    def get_permissions(self, user: dict) -> set[str]:
        """回傳該 user 具備的 business-key 權限集合（如 {'finding.review.update', ...}）。"""
        ...

    def check(self, user: dict, permission: str) -> bool:
        """該 user 是否具備某 business-key 權限。be2 provider 可覆寫為直接打 verify API。"""
        ...

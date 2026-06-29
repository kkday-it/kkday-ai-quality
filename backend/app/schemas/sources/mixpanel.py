"""mixpanel 源（mixpanel CSV / 第二階段 BQ）原始一列契約。

聚合類：埋點 event 計數（非逐筆）→ parser 後進 signal 表，不進 interaction。
注意：CSV 首行為 `# 來源:...` 註解行，connector/parser 需略過。
冪等鍵為 (event, breakdown_property, breakdown_value) 複合。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class MixpanelRow(BaseModel):
    """mixpanel 原始一列。"""

    model_config = ConfigDict(extra="ignore")

    event: str  # 必填
    breakdown_property: str | None = None
    breakdown_value: str | None = None
    count: str | None = None
    event_total: str | None = None

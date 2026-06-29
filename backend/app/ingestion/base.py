"""攝取管線共用型別（connector → parser 之間的資料契約）。

- RawRecord：connector 產出的「原始一列」（payload + 回源 raw_ref），下游 parser 與 BQ 模式共用。
- ParsedItem：parser 產出的正規化項（kind 區分 interaction/signal/product；data 為可建 ORM 的 dict；
  children 為附屬項，如對話拆出的多條 message）。

⚠️ WIP：app/ingestion/ 為新攝取架構（dlt 風格），尚未接上判決鏈（主力仍走 judge/ingest/）。
   本檔提供型別讓 parser/connector 可 import（不再啟動即崩）；實際落庫需配 repositories/（待 app/models ORM）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class RawRecord:
    """connector 產出的原始一列。payload＝欄位 dict；raw_ref＝回源座標（檔名/列號或 BQ 座標）。"""

    payload: dict[str, Any]
    raw_ref: dict[str, Any] = field(default_factory=dict)


@dataclass
class ParsedItem:
    """parser 產出的正規化項。

    kind：interaction（進線對話）/ signal（聚合埋點，不進 interaction）/ product（商品內容）。
    data：可直接建 ORM 的欄位 dict（不含 auto 欄位）。
    children：附屬正規化項（如 interaction 拆出的多條 message）。
    """

    kind: Literal["interaction", "signal", "product"]
    data: dict[str, Any]
    children: list[ParsedItem] = field(default_factory=list)

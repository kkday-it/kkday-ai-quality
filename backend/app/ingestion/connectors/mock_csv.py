"""MockCsvConnector：第一階段 mock 模式，串流讀 data/seeds/*.csv。

- 串流逐列（csv.DictReader lazy），72M/79M 大檔不爆記憶體
- 略過註解行（mixpanel CSV 首行為 `# 來源:...`）
- 產出 RawRecord（原始一列 dict + 回源 raw_ref），下游 parser 與 BQ 模式共用
第二階段對應的 BigQueryConnector 實作同一 fetch_raw 介面（讀 BQ），parser 以下不動。
"""

from __future__ import annotations

import csv
from collections.abc import Iterator
from pathlib import Path

from app.ingestion.base import RawRecord


class MockCsvConnector:
    """讀單一 CSV 檔，逐列產出 RawRecord。"""

    def __init__(self, csv_path: Path, skip_comment_prefix: str | None = None) -> None:
        self.csv_path = csv_path
        self.skip_comment_prefix = skip_comment_prefix

    def fetch_raw(
        self,
        since: str | None = None,
        until: str | None = None,
        cursor: str | None = None,
    ) -> Iterator[RawRecord]:
        # since/until/cursor：mock 模式忽略（全量）；第二階段 BQ 用於增量過濾
        with open(self.csv_path, encoding="utf-8-sig", newline="") as f:
            if self.skip_comment_prefix:
                pos = f.tell()
                line = f.readline()
                while line and line.startswith(self.skip_comment_prefix):
                    pos = f.tell()
                    line = f.readline()
                f.seek(pos)
            reader = csv.DictReader(f)
            for i, row in enumerate(reader):
                if not any((v or "").strip() for v in row.values()):
                    continue  # 跳過空列
                yield RawRecord(
                    payload={k: v for k, v in row.items() if k is not None},
                    raw_ref={"file": self.csv_path.name, "row": i},
                )

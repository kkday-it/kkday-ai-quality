"""連接器：mock（讀 CSV）/ 第二階段 live（BigQuery）。"""

from app.ingestion.connectors.mock_csv import MockCsvConnector

__all__ = ["MockCsvConnector"]

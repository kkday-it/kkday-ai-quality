"""各源 parser：原始 payload → ParsedItem（我們的標準）。"""

from app.ingestion.parsers.feedback import parse_feedback
from app.ingestion.parsers.mixpanel import parse_mixpanel
from app.ingestion.parsers.product import parse_product
from app.ingestion.parsers.review import parse_review
from app.ingestion.parsers.review_summary import parse_review_summary
from app.ingestion.parsers.session import parse_session
from app.ingestion.parsers.ticket import parse_ticket

__all__ = [
    "parse_review",
    "parse_session",
    "parse_ticket",
    "parse_feedback",
    "parse_review_summary",
    "parse_mixpanel",
    "parse_product",
]

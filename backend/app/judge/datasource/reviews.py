"""評論拉取：fetch_reviews → NormalizedTicket[]。

MVP 走 fixture（150665 已抓真實差評，零網路/零 key）；production 走 Review Service（內網，避 datadome）。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from app.core.schema import NormalizedTicket

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures"


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _sid(prod_id: str, body: str) -> str:
    h = hashlib.sha1(f"{prod_id}|{body}".encode()).hexdigest()[:12]
    return f"review-{prod_id}-{h}"


def fetch_reviews(
    prod_id: str, sort: str = "RATING_ASC", page: int = 1, source: str = "fixture"
) -> list[NormalizedTicket]:
    """拉商品評論（差評優先）。source=fixture（MVP）| live（production）。"""
    if source == "live":
        return _from_live(prod_id, sort, page)
    return _from_fixture(prod_id)


def _from_fixture(prod_id: str) -> list[NormalizedTicket]:
    fp = FIXTURES / f"product_{prod_id}.json"
    if not fp.exists():
        return []
    data = json.loads(fp.read_text(encoding="utf-8"))
    out: list[NormalizedTicket] = []
    for r in data.get("reviews", []):
        body = (str(r.get("title", "")) + " " + str(r.get("body", ""))).strip()
        out.append(
            NormalizedTicket(
                ticket_id=_sid(prod_id, body),
                source="review",
                prod_oid=str(prod_id),
                rating=r.get("rating"),
                comment=body,
                created_at=r.get("postDate", "") or _now(),
            )
        )
    return out


def _from_live(prod_id: str, sort: str, page: int) -> list[NormalizedTicket]:
    """production：打 Review Service（內網優先；正規憑證，不用 verify=False）。"""
    import httpx

    # TODO(prod)：改打內網 api-review.kkday.com/api/v1/product/reviews（避 datadome）
    url = "https://www.kkday.com/api/_nuxt/cpath/fetch-product-comments-v2"
    params = {"prodId": prod_id, "sort": sort, "page": page, "tags": ""}
    with httpx.Client(timeout=30) as c:
        resp = c.get(url, params=params, headers={"market": "zh-tw"})
        resp.raise_for_status()
        data = (resp.json() or {}).get("data", {})
    out: list[NormalizedTicket] = []
    for r in data.get("comments", []):
        title = (r.get("title") or {}).get("origin", "")
        body_o = (r.get("body") or {}).get("origin", "")
        body = (str(title) + " " + str(body_o)).strip()
        out.append(
            NormalizedTicket(
                ticket_id=f"review-{r.get('id')}",
                source="review",
                prod_oid=str(prod_id),
                rating=r.get("rating"),
                comment=body,
                created_at=r.get("postDate", ""),
            )
        )
    return out

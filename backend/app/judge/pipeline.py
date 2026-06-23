"""判決 pipeline 編排：NormalizedTicket → TicketFinding。

評論線：classify → (extract_fields) → adequacy → arbiter → diagnose。
"""

from __future__ import annotations

import hashlib

from app.core.schema import NormalizedTicket, TicketFinding
from app.judge import adequacy as L3
from app.judge import arbiter
from app.judge import classify as L2
from app.judge import diagnose as L4
from app.judge.datasource import product as ds_product


def diagnose_ticket(ticket: NormalizedTicket, prod_source: str = "fixture") -> TicketFinding:
    cls = L2.classify(ticket)
    dim = cls.get("dimension", "non_content")
    field = cls.get("suspected_field", "none")

    adeq: dict | None = None
    evidence = ""
    if dim == "non_content":
        verdict, conf = "escalate_ops", float(cls.get("confidence", 0.5))
    elif field == "none":
        verdict, conf = "customer_misread", 0.4
    else:
        pj = ds_product.fetch_product(ticket.prod_oid, source=prod_source)
        cfg = ds_product.extract_fields(ticket.prod_oid, pj)
        field_text = cfg.fields.get(field, "")
        adeq = L3.check(field_text, dim, cls.get("problem_summary", ""))
        evidence = adeq.get("evidence", "")
        verdict, conf = arbiter.reconcile(cls, adeq)

    action, detail, handoff = L4.build_action(verdict, cls)
    fid = "finding-" + hashlib.sha1(f"{ticket.ticket_id}|{verdict}".encode()).hexdigest()[:12]
    return TicketFinding(
        finding_id=fid,
        ticket_id=ticket.ticket_id,
        prod_oid=ticket.prod_oid,
        dimension=dim,
        problem_summary=cls.get("problem_summary", ""),
        suspected_field=field,
        evidence_quote=evidence,
        verdict=verdict,
        confidence=conf,
        recommended_action=action,
        action_detail=detail,
        writer_handoff=handoff,
        is_primary=bool(cls.get("is_primary", True)),
        status="new",
        created_at=ticket.created_at,
    )


def diagnose_many(tickets: list[NormalizedTicket], prod_source: str = "fixture") -> list[TicketFinding]:
    return [diagnose_ticket(t, prod_source=prod_source) for t in tickets]

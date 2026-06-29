"""售前售後進線 adapter：fetch_conversations → NormalizedTicket[]。

第一階段主力管道（取代評論為首發）。
- fixture（MVP）：讀 fixtures/conversations.json，零網路/零 BQ 權限，含客服對話 ground truth
- live（production）：BigQuery 售後聚合 SQL（dw_kkdb.message+chatbot）/ 售前 freshdesk_tickets，待 BQ 權限

售後 session_oid / 售前 freshdesk ticket id 為冪等鍵；cs_conversation 末筆 agent ＝客服政策原文（L3/L4 ground truth）。
評論（reviews.py）/ 工單 API 列後續迭代。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app.core.schema import CSTurn, NormalizedTicket

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures"


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def fetch_conversations(
    source: str = "fixture", since: str = "", until: str = ""
) -> list[NormalizedTicket]:
    """拉售前售後進線 → NormalizedTicket[]。source=fixture（MVP）| live（BQ，待權限）。"""
if source == "live":
        return _from_live(since, until)
    if source == "db":
        return _from_db()
    return _from_fixture()


def _parse_conversation(agg: str) -> tuple[list[CSTurn], str]:
    """aggregated_messages（每行「role: content」）→ (cs_conversation, 客訴文字)。

    role 前綴：user→customer；KKday 客服 / 供應商 / bot→agent。無前綴行視為上一輪續行。
    客訴文字＝所有 user 發話串接（classify 的 comment 輸入）。
    """
    turns: list[CSTurn] = []
    user_parts: list[str] = []
    for line in (agg or "").split("\n"):
        if ": " in line:
            prefix, content = line.split(": ", 1)
            role = "customer" if prefix.strip() == "user" else "agent"
            turns.append(CSTurn(role=role, content=content))
            if role == "customer":
                user_parts.append(content)
        elif turns:
            turns[-1].content += "\n" + line  # 續行接上一輪
            if turns[-1].role == "customer":
                user_parts.append(line)
    return turns, " ".join(user_parts).strip()


def _from_db() -> list[NormalizedTicket]:
    """本地 inquiries 表（merged CSV 灌入）→ NormalizedTicket[]。session_oid 為冪等鍵。"""
    from app.core.db import _conn

    with _conn() as c:
        rows = c.execute(
            "SELECT session_oid, sessionable_type, prod_oid, pkg_oid, order_oid, supplier_oid, "
            "session_create_date, aggregated_messages "
            "FROM inquiries WHERE aggregated_messages != '' AND aggregated_messages IS NOT NULL"
        ).fetchall()
    out: list[NormalizedTicket] = []
    for r in rows:
        cs, comment = _parse_conversation(r["aggregated_messages"])
        src = (
            r["sessionable_type"]
            if r["sessionable_type"] in ("order_message", "chatbot")
            else "order_message"
        )
        out.append(
            NormalizedTicket(
                ticket_id=str(r["session_oid"]),
                source=src,
                prod_oid=str(r["prod_oid"] or ""),
                pkg_oid=str(r["pkg_oid"] or ""),
                order_oid=str(r["order_oid"] or ""),
                supplier_oid=str(r["supplier_oid"] or ""),
                rating=None,
                comment=comment,
                cs_conversation=cs,
                created_at=r["session_create_date"] or _now(),
            )
        )
    return out


def _from_fixture() -> list[NormalizedTicket]:
    fp = FIXTURES / "conversations.json"
    if not fp.exists():
        return []
    data = json.loads(fp.read_text(encoding="utf-8"))
    out: list[NormalizedTicket] = []
    for t in data.get("tickets", []):
        cs = [
            CSTurn(role=c.get("role", ""), content=c.get("content", ""))
            for c in t.get("cs_conversation", [])
        ]
        out.append(
            NormalizedTicket(
                ticket_id=t["ticket_id"],
                source=t.get("source", "order_message"),
                prod_oid=str(t.get("prod_oid", "")),
                pkg_oid=str(t.get("pkg_oid", "")),
                rating=t.get("rating"),
                comment=t.get("comment", ""),
                cs_conversation=cs,
                created_at=t.get("created_at", "") or _now(),
            )
        )
    return out


def _from_live(since: str, until: str) -> list[NormalizedTicket]:
    """production：BigQuery 批次拉售後（message+chatbot 聚合）+ 售前（freshdesk）。

    待 BQ 讀取權限（Gary 申請中）。SQL 見 Confluence 子2 §六（已驗證）。
    聚合對話 aggregated_messages 依角色標記（K=客服/M=user/S=供應商）parse 成 cs_conversation。
    """
    raise NotImplementedError(
        "live 模式待 BigQuery 權限；現用 source=fixture 或 CSV 上傳（entry.py 已支援 aggregated_messages 別名）"
    )

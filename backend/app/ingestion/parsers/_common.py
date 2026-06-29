"""parser 共用 helpers：id 生成、內容雜湊、寬鬆型別轉換、對話拆解。

設計：parser 只負責「把各源原始欄位映射成我們的標準」，不碰 DB；
產出 ParsedItem.data 為可直接建 ORM 的 dict（不含 auto 欄位）。
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from typing import Any


def new_id() -> str:
    """生成 uuid 字串（interaction_id / message_id / signal_id ...）。"""
    return str(uuid.uuid4())


def clean(v: Any) -> str | None:
    """空字串 / None / 'nan' / 'null' 一律歸 None；其餘 strip。"""
    if v is None:
        return None
    s = str(v).strip()
    if s == "" or s.lower() in {"nan", "null", "none"}:
        return None
    return s


def to_float(v: Any) -> float | None:
    """寬鬆轉 float（CSV 值皆字串），失敗回 None。"""
    s = clean(v)
    if s is None:
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


# 容忍的時間格式（各源不一：2026-06-24 18:45:48 / 2026/6/24 13:20 / ISO 帶時區 ...）
_DT_FORMATS = (
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
    "%Y-%m-%d",
)


def to_dt(v: Any) -> datetime | None:
    """寬鬆解析時間字串為 datetime（無法解析回 None；保留原值由 source_metadata 兜底）。"""
    s = clean(v)
    if s is None:
        return None
    # 先試 ISO（Python 3.11+ fromisoformat 容忍度高）
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        pass
    for fmt in _DT_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def content_hash(*parts: Any) -> str | None:
    """以內容（+ 可選關聯）算 sha256，供 dedupe。全空回 None。"""
    joined = "|".join(clean(p) or "" for p in parts)
    if joined.strip("|") == "":
        return None
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def collect_metadata(payload: dict[str, Any], used: set[str]) -> dict[str, Any]:
    """把未映射到標準欄位的原始欄位收進 source_metadata（保留非空值）。"""
    out: dict[str, Any] = {}
    for k, v in payload.items():
        if k in used:
            continue
        cv = clean(v)
        if cv is not None:
            out[k] = cv
    return out


def split_conversation(agg: str | None) -> tuple[list[dict[str, Any]], str]:
    """aggregated_messages（每行 'role: content'）→ (messages[], 客訴文字)。

    role 前綴：user → customer；bot / 客服 / 供應商 / 其他 → agent。
    無前綴行視為上一輪續行。回傳 messages（含 seq）與 customer 發話串接（當 content）。
    """
    messages: list[dict[str, Any]] = []
    customer_parts: list[str] = []
    seq = 0
    for line in (agg or "").split("\n"):
        if ": " in line:
            prefix, body = line.split(": ", 1)
            role = "customer" if prefix.strip().lower() == "user" else "agent"
            messages.append({"message_id": new_id(), "author_role": role, "text": body, "seq": seq})
            seq += 1
            if role == "customer":
                customer_parts.append(body)
        elif line.strip() and messages:
            messages[-1]["text"] += "\n" + line  # 續行接上一輪
            if messages[-1]["author_role"] == "customer":
                customer_parts.append(line)
    return messages, " ".join(customer_parts).strip()

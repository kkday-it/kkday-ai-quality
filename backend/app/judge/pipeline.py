"""判決 pipeline 編排：NormalizedTicket → TicketFinding。

評論線：classify → (extract_fields) → adequacy → arbiter → diagnose
（含感知層來源 source_channel/source_system + 執行層 owner_role/exec_platform）。
"""

from __future__ import annotations

import hashlib

from app.core.schema import NormalizedTicket, TicketFinding
from app.judge import adequacy as L3
from app.judge import arbiter
from app.judge import classify as L2
from app.judge import codex
from app.judge import diagnose as L4
from app.judge.datasource import product as ds_product
from app.judge.vendored import machine_checks as mc

# 命名/行銷類欄位（codex.scan_misplacement 只掃這些；注意事項等揭露欄位不適用）
_MKT_FIELDS = ("prod_name", "prod_feature", "prod_summary", "pkg_desc")

# 感知層：NormalizedTicket.source → (source_channel, source_system)
_SOURCE_MAP: dict[str, tuple[str, str]] = {
    "review": ("B_customer", "商品評論"),
    "ticket": ("B_customer", "FreshDesk 工單"),
    "order_message": ("B_customer", "訂單訊息"),
    "chatbot": ("B_customer", "AI 客服進線"),
    "manual": ("unknown", "人工錄入"),
}

_VALID_DIMS = {
    "商品定位", "行程流程", "費用資訊", "集合資訊",
    "使用兌換", "成團條件", "限制與風險", "承諾與SLA", "non_content",
}
# adequacy 查詢用的 logical field（對齊 datasource.product.LOGICAL_FIELDS）；codex 細欄名依 dimension 回退到這些
_LOGICAL = {
    "prod_name", "prod_summary", "prod_feature", "prod_schedules", "prod_notice",
    "prod_fee", "prod_meetup", "prod_redeem", "prod_purchase", "pkg_desc", "pkg_schedules",
}
_DIM_FIELD: dict[str, str] = {
    "商品定位": "prod_name",
    "行程流程": "prod_schedules",
    "費用資訊": "prod_summary",
    "集合資訊": "prod_summary",
    "使用兌換": "pkg_desc",
    "成團條件": "prod_notice",
    "限制與風險": "prod_notice",
    "承諾與SLA": "prod_summary",
}


def _as_str(v: object) -> str:
    """LLM 可能回 list/dict/None → 一律轉字串（Pydantic str 欄位防呆）。"""
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        return " ".join(_as_str(x) for x in v)
    return str(v)


def _sanitize_classify(cls: dict) -> dict:
    """LLM 輸出防呆：dimension 非法→non_content、suspected_field None→none、summary/confidence/is_primary 補位。"""
    cls = dict(cls or {})
    dim = cls.get("dimension")
    cls["dimension"] = dim if dim in _VALID_DIMS else "non_content"
    cls["suspected_field"] = str(cls.get("suspected_field") or "none")
    cls["problem_summary"] = str(cls.get("problem_summary") or "")
    try:
        cls["confidence"] = float(cls.get("confidence") or 0.5)
    except (TypeError, ValueError):
        cls["confidence"] = 0.5
    cls["is_primary"] = bool(cls.get("is_primary", True))
    return cls


def diagnose_ticket(ticket: NormalizedTicket, prod_source: str = "fixture") -> TicketFinding:
    cls = _sanitize_classify(L2.classify(ticket))
    dim = cls["dimension"]
    field = cls["suspected_field"]  # 可能是 codex 細欄名（LLM）或 7 logical field（stub）

    # 客服對話末筆 agent ＝ ground truth（售前售後進線政策原文，零幻覺；評論無對話則空）
    gt = next((t.content for t in reversed(ticket.cs_conversation) if t.role == "agent"), "")

    adeq: dict | None = None
    evidence = ""
    hit_rule_id = ""
    if dim == "non_content":
        verdict, conf = "escalate_ops", float(cls.get("confidence", 0.5))
    elif not field or field == "none":
        verdict, conf = "customer_misread", 0.4
    else:
        pj = ds_product.fetch_product(ticket.prod_oid, source=prod_source)
        cfg = ds_product.extract_fields(ticket.prod_oid, pj)
        # codex 細欄名（LLM）無法直接查 logical field → 依 dimension 回退到對應邏輯欄位
        logical = field if field in _LOGICAL else _DIM_FIELD.get(dim, "prod_summary")
        field_text = cfg.fields.get(logical, "")
        # 第一道確定性閘門（零 LLM，vendored machine_checks）：禁詞/長度/促銷/結構/空欄
        machine = mc.check_field(logical, field_text, ticket.lang, gt)
        # 法典確定性層（零 LLM，codex R5-2/R5-3）：命名/行銷欄位掃錯位成團/行銷關鍵字
        mkt_text = " ".join(cfg.fields.get(k, "") for k in _MKT_FIELDS)
        rule_hits = codex.scan_misplacement(mkt_text)
        # 欄位原文為空 → 不呼叫 LLM（深度 prompt 可達 9k 字，空欄呼叫純浪費）；machine empty_output 已足夠定調
        if field_text.strip():
            adeq = L3.check(field_text, dim, cls.get("problem_summary", ""), field=field, ground_truth=gt)
        else:
            adeq = {"status": "field_empty", "evidence": "（該欄位未取得原文）", "reason": "欄位原文為空"}
        evidence = _as_str(adeq.get("evidence", "")) or (machine[0].get("evidence", "") if machine else "")
        verdict, conf = arbiter.reconcile(cls, adeq, machine, rule_hits)
        # 法典溯源：錯位規則直接命中 → 該 rule_id；否則 content_missing 回填該面向空欄規則
        if rule_hits:
            hit_rule_id = rule_hits[0]["rule_id"]
        elif verdict == "content_missing":
            hit_rule_id = codex.empty_rule_for(dim)

    action, detail, handoff = L4.build_action(verdict, cls)
    owner_role, exec_platform = L4.build_exec(verdict)
    channel, system = _SOURCE_MAP.get(ticket.source, ("unknown", ticket.source))
    fid = "finding-" + hashlib.sha1(f"{ticket.ticket_id}|{verdict}".encode()).hexdigest()[:12]
    return TicketFinding(
        finding_id=fid,
        ticket_id=ticket.ticket_id,
        prod_oid=ticket.prod_oid,
        pkg_oid=ticket.pkg_oid,
        order_oid=ticket.order_oid,
        supplier_oid=ticket.supplier_oid,
        dimension=dim,
        problem_summary=cls.get("problem_summary", ""),
        suspected_field=field,
        evidence_quote=evidence,
        ground_truth_quote=gt,
        verdict=verdict,
        confidence=conf,
        recommended_action=action,
        action_detail=detail,
        writer_handoff=handoff,
        is_primary=bool(cls.get("is_primary", True)),
        hit_rule_id=hit_rule_id,
        status="new",
        created_at=ticket.created_at,
        source_channel=channel,
        source_system=system,
        owner_role=owner_role,
        exec_platform=exec_platform,
    )


def diagnose_many(tickets: list[NormalizedTicket], prod_source: str = "fixture") -> list[TicketFinding]:
    out: list[TicketFinding] = []
    for t in tickets:
        try:
            out.append(diagnose_ticket(t, prod_source=prod_source))
        except Exception as e:  # noqa: BLE001 — LLM 輸出不可預測，單筆失敗不毀整批
            print(f"⚠️ ticket {t.ticket_id} 判決失敗，略過：{e}")
    return out

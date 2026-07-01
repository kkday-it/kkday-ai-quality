"""初判歸因單條引擎：一條進線資料 → TicketFinding（極性閘門 → 候選域 canon 聚焦歸因）。

accuracy/token 最優管線（真實 83k 筆精算，見 plans/toasty-churning-shell.md）：
- Stage 0 零 LLM 略過純好評（rating=5 + 評論極短 + 無負向詞）→ $0，不歸因。
- Stage 1 極性閘門（便宜模型 / stub 啟發式）：正向・中性 → 不歸因收尾。
- Stage 2 歸因（負向；單次呼叫 + 候選域 L3 catalog 聚焦）：選 l3_code + 信心。
- Stage 2b 自適應複判（僅信心落 jury 帶）：注入該 L2 完整 canon 再判一次，取信心較高者。

判準來源一律 core/ai_judge（rule_C-*.json 的 canon/allow/forbid/正反例），禁在此自寫判準。
finding 為純歸因（軸A）：polarity + L1/L2/L3 + confidence + recommended_action；verdict（軸B）已自
schema.TicketFinding 移除，本引擎不產 verdict，recommended_action 由歸因域推導。
無 token（client.is_stub）→ 全程啟發式，model_used="stub"，讓零 key 也跑通閉環。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, get_args

from app.core import ai_judge
from app.core.paths import AI_JUDGE_DIR
from app.core.schema import CONTENT_DIMENSIONS, RecommendedAction, TicketFinding
from app.judge.llm import client

# ── config（judgment.json：信心閾值 + prejudge 旋鈕）；lazy 快取 ──────────────
_cfg_cache: dict | None = None


def _cfg() -> dict:
    """讀 config/ai_judge/judgment.json（信心閾值 + prejudge 旋鈕）；缺檔回內建預設。"""
    global _cfg_cache
    if _cfg_cache is None:
        try:
            _cfg_cache = json.loads((AI_JUDGE_DIR / "judgment.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _cfg_cache = {}
    return _cfg_cache


def reload_config() -> None:
    """清 config 快取（judgment.json 線上編輯後呼叫，使新閾值/旋鈕即時生效）。"""
    global _cfg_cache
    _cfg_cache = None


def _tiers() -> dict:
    return _cfg().get("confidence_tiers", {"auto_accept": 0.8, "jury_low": 0.5, "jury_high": 0.7})


def _prejudge_cfg() -> dict:
    return _cfg().get("prejudge", {})


# 歸因域 → 建議行動 SSOT＝各 rule_C-*.json 的 _meta.recommended_action（ai_judge.domain_action 讀）；
# 不再於此硬編碼（舊 dict 用已廢域名 order/platform/cs，現行 product_quality/redemption/service 查無而失準）。
_VALID_ACTIONS: frozenset[str] = frozenset(get_args(RecommendedAction))

# content 域 L2 label 關鍵詞 → 舊 8 面向 Dimension（TicketFinding.dimension 必填、為 legacy 欄）。
# dim label 引用 schema.CONTENT_DIMENSIONS（單一真相源，不再手打中文）；關鍵詞為本檔專有比對規則。
# 新流程真實信號在 l1/l2/l3_code；dimension 僅為相容既有彙總，低權重。無匹配→非內容用 non_content。
_CONTENT_DIM_KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    (("定位", "名稱", "特色", "摘要"), CONTENT_DIMENSIONS[0]),  # 商品定位
    (("行程", "流程", "步驟"), CONTENT_DIMENSIONS[1]),  # 行程流程
    (("費用", "價格", "包含", "退款"), CONTENT_DIMENSIONS[2]),  # 費用資訊
    (("集合", "地點", "交通"), CONTENT_DIMENSIONS[3]),  # 集合資訊
    (("兌換", "使用", "憑證", "voucher"), CONTENT_DIMENSIONS[4]),  # 使用兌換
    (("成團", "人數"), CONTENT_DIMENSIONS[5]),  # 成團條件
    (("限制", "風險", "年齡", "安全"), CONTENT_DIMENSIONS[6]),  # 限制與風險
    (("承諾", "sla", "保證"), CONTENT_DIMENSIONS[7]),  # 承諾與SLA
]


def _now_iso() -> str:
    """UTC ISO 時間（判決/建立時間戳）。"""
    return datetime.now(timezone.utc).isoformat()


def _text_of(item: dict) -> str:
    """取判決主輸入文字：優先 comment，回退 raw 內常見文字欄。"""
    txt = (item.get("comment") or "").strip()
    if txt:
        return txt
    raw = item.get("raw") or {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            raw = {}
    for k in ("content", "comment", "aggregated_messages", "feedback"):
        v = raw.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _neg_keywords() -> list[str]:
    return _prejudge_cfg().get("neg_keywords", [])


def _has_neg_kw(text: str) -> bool:
    """文字是否含負向關鍵詞（stub/Stage0 判負向用）。"""
    return any(kw in text for kw in _neg_keywords())


def _dimension_for(l1_domain: str, l2_label: str) -> str:
    """L1 域 + L2 label → 舊 8 面向 Dimension（相容欄）；非內容域回 non_content。"""
    if l1_domain != "content":
        return "non_content"
    low = (l2_label or "").lower()
    for kws, dim in _CONTENT_DIM_KEYWORDS:
        if any(k in low for k in kws):
            return dim
    return CONTENT_DIMENSIONS[0]  # content 域無匹配時的保守預設（Literal 需合法值）


def _action_for(l1_domain: str) -> str:
    """歸因域 → recommended_action（讀 config；未歸類 / 未知域回 escalate_ux）。"""
    action = ai_judge.domain_action(l1_domain)
    return action if action in _VALID_ACTIONS else "no_action"


def _tier_for(conf: float) -> str:
    """信心 → 分層：>=auto_accept 自動採信 / <jury_low 待人審 / 之間 評審覆核。"""
    t = _tiers()
    if conf >= t.get("auto_accept", 0.8):
        return "auto_accept"
    if conf < t.get("jury_low", 0.5):
        return "needs_review"
    return "jury"


def _in_jury_band(conf: float) -> bool:
    """信心是否落自適應複判帶 [jury_low, jury_high)。"""
    t = _tiers()
    return t.get("jury_low", 0.5) <= conf < t.get("jury_high", 0.7)


def _evidence_cap(l1_domain: str, item: dict, raw_conf: float) -> float:
    """證據封頂：無訂單證據不得高信心判供應商履約（供應商域需訂單佐證）。

    對齊 SSOT「evidence-gated」：進線多為 symptom_only（評論無訂單），故 supplier 域在缺
    order_oid 時封頂至 jury_high 以下，逼入評審/人審而非自動採信。
    """
    has_order = bool(item.get("order_oid") or (item.get("raw") or {}).get("order_oid"))
    if l1_domain == "supplier" and not has_order:
        return min(raw_conf, _tiers().get("jury_high", 0.7) - 0.01)
    return raw_conf


# ── 分模型呼叫（Stage1 便宜 / Stage2 主模型）；覆寫 contextvar model，不改 client.py ──
def _stage1_model(main_model: str) -> str:
    """極性閘門模型：OpenAI provider 用 config stage1_model（同 token），否則回退主模型。"""
    from app.core import settings as app_settings

    cur = app_settings.current()
    if app_settings.provider_id_for(cur.get("base_url", "")) != "openai":
        return main_model
    return _prejudge_cfg().get("stage1_model") or main_model


def _call(system: str, user: str, stage: str, model: str, *, schema: dict | None = None) -> dict:
    """呼叫 LLM；暫時覆寫 contextvar 的 model 為本階段模型，呼叫後還原（thread-local 安全）。

    schema 傳入時走 Structured Outputs（強制 l3_code 只吐候選白名單合法 code）。
    """
    from app.core import settings as app_settings

    cur = app_settings.current()
    if model and model != cur.get("model"):
        app_settings.set_current({**cur, "model": model})
        try:
            return client.chat_json(system, user, stage, schema=schema)
        finally:
            app_settings.set_current(cur)
    return client.chat_json(system, user, stage, schema=schema)


def _attr_schema(candidate_codes: frozenset[str]) -> dict:
    """Stage2 歸因輸出 schema（OpenAI Structured Outputs strict）。

    l3_code enum＝候選域全部合法 code + 空字串（abstain）；生成階段即保證不吐非法 code。
    strict 模式要求每層 additionalProperties=false 且 required 含全部欄位。
    """
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["l3_code", "confidence", "evidence_quote", "candidates"],
        "properties": {
            "l3_code": {"type": "string", "enum": [*sorted(candidate_codes), ""]},
            "confidence": {"type": "number"},
            "evidence_quote": {"type": "string"},
            "candidates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["code", "score"],
                    "properties": {
                        "code": {"type": "string"},
                        "score": {"type": "number"},
                    },
                },
            },
        },
    }


def _evidence_grounded(text: str, quote: str) -> bool:
    """LLM 回的 evidence_quote 是否確為原文片段（防編造證據）。

    正規化去空白後 substring 比對；片段過短（<4 字）視為未有效佐證。用於「證據不足 → 不自動採信」。
    """
    q = re.sub(r"\s+", "", quote or "")
    t = re.sub(r"\s+", "", text or "")
    return len(q) >= 4 and q in t


# ── prompt builders（純函式；判準一律取自 ai_judge，禁自寫）─────────────────
_POLARITY_SYS = (
    "你是 KKday 旅遊商品進線極性判官。只判斷整體情緒傾向，不做任何歸因。輸出 JSON。"
)

# 判準指引由 GEPA（gpt-4.1-mini proxy 優化，valset 58.6%→69.5%）精煉 + 人工適配保留 JSON 輸出。
# 核心：域優先 + 明示界線（content↔供應商履約、商品品質↔客服）+ 寧缺勿濫，直擊本專案主要誤判源。
_ATTR_SYS = (
    "你是 KKday 旅遊商品客訴歸因判官。依『問題分類 L3 目錄』把這則負向評論嚴格且精準歸到最貼切的一條 code，並給信心。\n"
    "判斷流程：①先定主體問題域（商品內容／商品品質／供應商履約／使用兌換／客服營運／客人理解）②再依細項定義選最貼切 code。\n"
    "界線鐵則：\n"
    "- 商品內容＝商品頁描述／定位／行程／費用「寫的與實際不符」；供應商履約＝執行面「沒依約做到」（臨時取消、變更、接送遲到、人員態度）。\n"
    "- 商品品質＝產品本身實際品質（網路訊號、餐飲、車輛設備）；客服營運＝訂單售後互動（退款、回應、態度）。\n"
    "- 客人理解＝旅客自身期待或誤解造成、無可歸責商家。\n"
    "多個問題時取最核心、最直接的抱怨點，勿被次要問題誤導。只能從目錄內選 code；無法明確歸類時 l3_code 回空字串（寧缺勿濫）。輸出 JSON。"
)


def _l3_catalog(domains: list[dict]) -> str:
    """候選域的 L3 精簡目錄（code | 域›面向›細項 | 意義≤40字）；Stage2 單次呼叫注入。"""
    lines: list[str] = []
    codes = [d["code"] for d in domains]
    for n in ai_judge.l3_nodes_for_domains(codes):
        meaning = (n.get("meaning") or "")[:40]
        # 變深度：路徑動態串接（L2 葉無 l3_label → 只印 域›面向），省略空層
        path = "›".join(p for p in (n.get("l1_label", ""), n.get("l2_label", ""), n.get("l3_label", "")) if p)
        lines.append(f"{n['code']} | {path} | {meaning}")
    return "\n".join(lines)


def _l2_canon_block(l2_code: str) -> str:
    """某 L2 面向下所有 L3 的完整厚判準（canon/allow/forbid/正反例）；Stage2b 聚焦複判注入。"""
    lines: list[str] = []
    for n in ai_judge.l3_nodes_for_domains([]):  # 全量後過濾該 L2（節點帶 l2_code）
        if n.get("l2_code") != l2_code:
            continue
        lines.append(
            f"[{n['code']}] {n.get('l3_label') or n.get('l2_label', '')}\n"
            f"  canon: {n.get('canon', '')}\n"
            f"  允許: {'；'.join(n.get('allow', []))}\n"
            f"  禁止: {'；'.join(n.get('forbid', []))}\n"
            f"  正例: {'；'.join(n.get('positive_cases', []))}\n"
            f"  反例: {'；'.join(n.get('negative_cases', []))}"
        )
    return "\n".join(lines)


def _attr_user(text: str, catalog: str) -> str:
    """Stage2 歸因 user prompt：進線文字 + L3 目錄 + 輸出格式。"""
    return (
        f"進線文字：\n{text}\n\n"
        f"問題分類 L3 目錄（只能從中選 code）：\n{catalog}\n\n"
        "輸出 JSON：{\"l3_code\":\"最貼切的一條 code（無法歸類回空字串）\","
        "\"confidence\":0~1 浮點,"
        "\"evidence_quote\":\"進線中最能佐證的原文片段\","
        "\"candidates\":[{\"code\":\"code\",\"score\":0~1},...最多3條]}"
    )


def _rejudge_user(text: str, canon_block: str) -> str:
    """Stage2b 聚焦複判 user prompt：進線文字 + 該 L2 完整厚判準 + 輸出格式。"""
    return (
        f"進線文字：\n{text}\n\n"
        f"候選 L3 完整判準（canon/允許/禁止/正反例）：\n{canon_block}\n\n"
        "依上述判準再判一次，輸出 JSON："
        "{\"l3_code\":\"code\",\"confidence\":0~1,\"evidence_quote\":\"原文片段\"}"
    )


# ── stub 啟發式（無 token 時零 key 跑通閉環；佔位非真值）─────────────────────
def _stub_polarity(item: dict, text: str) -> str:
    """rating + 負向關鍵詞 啟發式極性（stub）。"""
    r = item.get("rating")
    if isinstance(r, int):
        if r <= 2:
            return "negative"
        if r >= 4:
            return "positive"
    if _has_neg_kw(text):
        return "negative"
    return "neutral" if not text else "unknown"


# ── 解析與淨化 ──────────────────────────────────────────────────────────────
def _as_float(v: Any, default: float = 0.0) -> float:
    """寬鬆轉 float（LLM 可能回字串）；失敗回 default，夾到 [0,1]。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, f))


def _sanitize_l3(l3_code: str, candidate_codes: frozenset[str]) -> dict[str, str]:
    """校驗 l3_code ∈ 候選白名單並回填 l1/l2/l3 label；非法回全空（未歸類）。"""
    if l3_code and l3_code in candidate_codes:
        n = ai_judge.l3_by_code(l3_code) or {}
        # 變深度：L3 葉→完整回填；L2 葉→l2 填葉本身、l3 留空（進 by_l2 不進 by_l3）
        is_l3_leaf = n.get("level") == 3
        return {
            "l1_domain_code": n.get("l1_domain", ""),
            "l1_label": n.get("l1_label", ""),
            "l2_code": n.get("l2_code", ""),
            "l2_label": n.get("l2_label", ""),
            "l3_code": l3_code if is_l3_leaf else "",
            "l3_label": n.get("l3_label", "") if is_l3_leaf else "",
        }
    return {k: "" for k in ("l1_domain_code", "l1_label", "l2_code", "l2_label", "l3_code", "l3_label")}


# ── finding 組裝 ────────────────────────────────────────────────────────────
def _base_kwargs(item: dict) -> dict:
    """TicketFinding 共用簿記欄（id/來源/時間/oid）。"""
    item_id = item.get("item_id", "")
    now = _now_iso()
    return {
        "finding_id": f"fd_{item_id}",  # 冪等：重判 upsert 覆蓋
        "ticket_id": item_id,
        "prod_oid": item.get("prod_oid", "") or "",
        "pkg_oid": item.get("pkg_oid", "") or "",
        "order_oid": item.get("order_oid", "") or "",
        "status": "new",
        "created_at": now,
        "judged_at": now,
    }


def _non_issue_finding(item: dict, polarity: str, model: str) -> TicketFinding:
    """正向/中性 → 不歸 L1-L3、不進問題清單（純非問題）。"""
    conf = 1.0 if polarity in ("positive", "neutral") else 0.5
    return TicketFinding(
        **_base_kwargs(item),
        dimension="non_content",
        recommended_action="no_action",
        polarity=polarity,
        confidence=conf,
        raw_confidence=conf,
        confidence_tier="auto_accept" if polarity != "unknown" else "needs_review",
        model_used=model,
    )


def _attributed_finding(item: dict, attr: dict, model: str, *, enhanced: bool) -> TicketFinding:
    """負向歸因 finding（Stage2/2b 產出 → TicketFinding）。attr 為淨化後 dict。"""
    conf = attr["confidence"]
    tier = _tier_for(conf)
    return TicketFinding(
        **_base_kwargs(item),
        dimension=_dimension_for(attr["l1_domain_code"], attr["l2_label"]),
        recommended_action=_action_for(attr["l1_domain_code"]),
        problem_summary=attr.get("evidence_quote", "")[:200],
        evidence_quote=attr.get("evidence_quote", ""),
        polarity="negative",
        confidence=conf,
        raw_confidence=attr.get("raw_confidence", conf),
        confidence_tier=tier,
        needs_review=tier == "needs_review",
        is_enhanced=enhanced,
        enhance_model=model if enhanced else "",
        l1_domain_code=attr["l1_domain_code"],
        l1_label=attr["l1_label"],
        l2_code=attr["l2_code"],
        l2_label=attr["l2_label"],
        l3_code=attr["l3_code"],
        l3_label=attr["l3_label"],
        l3_candidates=attr.get("l3_candidates", []),
        model_used=model,
    )


# ── 各階段 ──────────────────────────────────────────────────────────────────
def _skip0(item: dict, text: str) -> bool:
    """Stage0 零 LLM 略過：純好評（rating=5 + 評論極短 + 無負向詞）。"""
    cfg = _prejudge_cfg()
    if not cfg.get("enable_stage0_skip", True):
        return False
    return (
        item.get("rating") == 5
        and len(text) <= cfg.get("stage0_max_comment_len", 8)
        and not _has_neg_kw(text)
    )


def _stage1_polarity(item: dict, text: str, main_model: str) -> str:
    """Stage1 極性閘門：stub 走啟發式，否則便宜模型 LLM。"""
    if client.is_stub():
        return _stub_polarity(item, text)
    out = _call(
        _POLARITY_SYS,
        f"文字：\n{text}\n\n輸出 JSON：{{\"polarity\":\"positive|negative|neutral\"}}",
        "polarity",
        _stage1_model(main_model),
    )
    pol = str(out.get("polarity", "")).strip().lower()
    return pol if pol in ("positive", "negative", "neutral") else "unknown"


def _candidate_codes() -> frozenset[str]:
    """候選域（排除 intake_excluded）底下全部 L3 code 白名單。"""
    domains = ai_judge.selectable_domains()
    return frozenset(n["code"] for n in ai_judge.l3_nodes_for_domains([d["code"] for d in domains]))


def _stage2_attribute(item: dict, text: str, model: str) -> dict:
    """Stage2 歸因（負向·單次呼叫）→ 淨化後 attr dict（含 raw_confidence）。"""
    domains = ai_judge.selectable_domains()
    candidate_codes = frozenset(
        n["code"] for n in ai_judge.l3_nodes_for_domains([d["code"] for d in domains])
    )
    if client.is_stub():  # stub 佔位：未歸類、中信心、待人審
        base = _sanitize_l3("", candidate_codes)
        return {**base, "confidence": 0.5, "raw_confidence": 0.5,
                "evidence_quote": text[:120], "l3_candidates": []}
    out = _call(
        _ATTR_SYS, _attr_user(text, _l3_catalog(domains)), "attribute", model,
        schema=_attr_schema(candidate_codes),
    )
    resolved = _sanitize_l3(str(out.get("l3_code", "")).strip(), candidate_codes)
    raw_conf = _as_float(out.get("confidence"), 0.5)
    conf = _evidence_cap(resolved["l1_domain_code"], item, raw_conf)
    evidence = str(out.get("evidence_quote", ""))[:300]
    # 證據門檻：已歸類但 evidence_quote 非原文逐字片段（疑編造）→ 封頂至 needs_review 以下，
    # 不自動採信未佐證的歸因（對齊使用者「證據不足跳過」；保留 finding 交人審，不硬丟）。
    if resolved["l3_code"] and not _evidence_grounded(text, evidence):
        conf = min(conf, _tiers().get("jury_low", 0.5) - 0.01)
    cands = [
        {"code": c.get("code", ""), "score": _as_float(c.get("score"))}
        for c in (out.get("candidates") or [])[:3]
        if isinstance(c, dict)
    ]
    return {**resolved, "confidence": conf, "raw_confidence": raw_conf,
            "evidence_quote": evidence, "l3_candidates": cands}


def _stage2b_rejudge(item: dict, text: str, prev: dict, model: str) -> dict:
    """Stage2b 自適應複判（僅 jury 帶）：注入該 L2 完整判準再判，取信心較高者。"""
    l2_code = prev.get("l2_code", "")
    if not l2_code or client.is_stub():
        return prev
    candidate_codes = frozenset(
        n["code"] for n in ai_judge.l3_nodes_for_domains([prev.get("l1_domain_code", "")])
    )
    # 複判 schema：l3_code 限該 L1 域合法 code（無 candidates 欄，對齊 _rejudge_user 輸出）
    rj_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["l3_code", "confidence", "evidence_quote"],
        "properties": {
            "l3_code": {"type": "string", "enum": [*sorted(candidate_codes), ""]},
            "confidence": {"type": "number"},
            "evidence_quote": {"type": "string"},
        },
    } if candidate_codes else None
    out = _call(_ATTR_SYS, _rejudge_user(text, _l2_canon_block(l2_code)), "rejudge", model, schema=rj_schema)
    new_conf = _as_float(out.get("confidence"), 0.0)
    if new_conf <= prev["confidence"]:
        return prev  # 複判沒更有把握 → 保留原判
    resolved = _sanitize_l3(str(out.get("l3_code", "")).strip(), candidate_codes) if candidate_codes else {}
    merged = {k: v for k, v in resolved.items() if v} if resolved else {}
    conf = _evidence_cap(merged.get("l1_domain_code", prev["l1_domain_code"]), item, new_conf)
    return {**prev, **merged, "confidence": conf, "raw_confidence": new_conf,
            "evidence_quote": str(out.get("evidence_quote", prev.get("evidence_quote", "")))[:300]}


def to_finding(item: dict, *, model: str) -> TicketFinding:
    """一條進線資料 → TicketFinding（完整四階段管線）。

    Args:
        item: intake_items 列 dict（item_id/source/prod_oid/rating/comment/raw/order_oid…）。
        model: 主判決模型（Stage2/2b）；stub 模式忽略、走啟發式。

    Returns:
        判決單元 TicketFinding（正向不歸因 / 負向帶 L1-L3 歸因）。
    """
    used_model = "stub" if client.is_stub() else model
    text = _text_of(item)

    # Stage 0：零 LLM 略過純好評
    if _skip0(item, text):
        return _non_issue_finding(item, "positive", "heuristic")

    # Stage 1：極性閘門
    polarity = _stage1_polarity(item, text, model)
    if polarity != "negative":
        return _non_issue_finding(item, polarity, used_model)

    # Stage 2：歸因（負向）
    attr = _stage2_attribute(item, text, model)

    # Stage 2b：自適應複判（僅 jury 帶且開啟）
    if _prejudge_cfg().get("enable_adaptive_rejudge", True) and _in_jury_band(attr["confidence"]):
        enhanced = _stage2b_rejudge(item, text, attr, model)
        if enhanced is not attr:
            return _attributed_finding(item, enhanced, used_model, enhanced=True)

    return _attributed_finding(item, attr, used_model, enhanced=False)

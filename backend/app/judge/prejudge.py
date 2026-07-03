"""初判歸因單條引擎：一條進線資料 → TicketFinding（極性閘門 → 候選域 canon 聚焦歸因）。

accuracy/token 最優管線（真實 83k 筆精算，見 plans/toasty-churning-shell.md）：
- Stage 0 零 LLM 略過純好評（rating=5 + 評論極短 + 無負向詞）→ $0，不歸因。
- Stage 1 極性閘門（便宜模型 / stub 啟發式）：正向・中性 → 不歸因收尾。
- Stage 2 歸因（負向；單次呼叫 + 候選域 L3 catalog 聚焦）：選 l3_code + 信心。
- Stage 2b 自適應複判（僅信心落 jury 帶）：注入該 L2 完整 canon 再判一次，取信心較高者。

判準來源一律 core/ai_judge（rule_C-*.json 的 canon/allow/forbid/好壞範例），禁在此自寫判準。
finding 為純歸因（軸A）：polarity + L1/L2/L3 + confidence + recommended_action；verdict（軸B）已自
schema.TicketFinding 移除，本引擎不產 verdict，recommended_action 由歸因域推導。
無 token（client.is_stub）→ 全程啟發式，model_used="stub"，讓零 key 也跑通閉環。
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any, get_args

from app.core import ai_judge, global_rule
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
    """取判決主輸入文字：優先 comment（intake_items）/ content（product_reviews 專表頂層欄），回退 raw 內常見文字欄。"""
    txt = (item.get("comment") or item.get("content") or "").strip()
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


def _evidence_capped(l1_domain: str, item: dict) -> bool:
    """是否因缺外部佐證觸發證據封頂（現行：供應商域缺 order_oid）。

    Phase B 會改讀 global_rule.evidence_policy().caps 使多域可配置（如 content 域缺商品頁快照）。
    判決階段派生用此旗標把「需外部資料才能定論」的案子導向 pending_data（待數據補充）。
    """
    has_order = bool(item.get("order_oid") or (item.get("raw") or {}).get("order_oid"))
    return l1_domain == "supplier" and not has_order


def _evidence_cap(l1_domain: str, item: dict, raw_conf: float) -> float:
    """證據封頂：無訂單證據不得高信心判供應商履約（供應商域需訂單佐證）。

    對齊 SSOT「evidence-gated」：進線多為 symptom_only（評論無訂單），故 supplier 域在缺
    order_oid 時封頂至 jury_high 以下，逼入評審/人審而非自動採信。
    """
    if _evidence_capped(l1_domain, item):
        return min(raw_conf, _tiers().get("jury_high", 0.7) - 0.01)
    return raw_conf


def _derive_stage(polarity: str, l3_code: str, tier: str, evidence_capped: bool) -> str:
    """判決階段派生（單一真相；未判＝無 finding 於 db enrich 層補 "unjudged"）。

    - insufficient 資訊不足：連傾向都判不出（unknown），評論本身太少，外部資料救不了。
    - judged 已判決：非負向(non_issue) 或 負向+歸到 L3+高信心+未觸 cap。
    - pending_data 待數據補充：負向但 L3 空(abstain) 或 evidence-cap 觸發(缺訂單/商品頁)——需外部佐證、能救。
    - pending_review 待覆核：負向+有 L3+信心不足(jury/needs_review)+未觸 cap——有候選、靠人審。
    """
    if polarity == "unknown":
        return "insufficient"
    if polarity != "negative":  # positive / neutral → non_issue
        return "judged"
    if evidence_capped or not l3_code:
        return "pending_data"
    return "judged" if tier == "auto_accept" else "pending_review"


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
            return client.chat_json(system, user, stage, schema=schema, cache_key=stage)
        finally:
            app_settings.set_current(cur)
    return client.chat_json(system, user, stage, schema=schema, cache_key=stage)


def _attr_schema(candidate_codes: frozenset[str], *, allow_empty: bool = True) -> dict:
    """Stage2 歸因輸出 schema（OpenAI Structured Outputs strict）。

    l3_code enum＝候選域全部合法 code（+ 空字串 abstain，`allow_empty=True` 時）；生成階段即保證不吐非法 code。
    cascade Stage B 傳 `allow_empty=False`＝enum 不含空 → 強制選一 leaf code（保證負向 ≥ L1+L2）。
    strict 模式要求每層 additionalProperties=false 且 required 含全部欄位。
    """
    l3_enum = [*sorted(candidate_codes), ""] if allow_empty else sorted(candidate_codes)
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["l3_code", "confidence", "evidence_quote", "candidates"],
        "properties": {
            "l3_code": {"type": "string", "enum": l3_enum},
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
    """候選域的 L3 精簡目錄（code | 域›面向›細項 | 判準≤40字）；Stage2 單次呼叫注入。"""
    lines: list[str] = []
    codes = [d["code"] for d in domains]
    for n in ai_judge.l3_nodes_for_domains(codes):
        desc = (n.get("canon") or "")[:40]  # 原用 meaning，欄位精煉後改取 canon 首段當短描述
        # 變深度：路徑動態串接（L2 葉無 l3_label → 只印 域›面向），省略空層
        path = "›".join(p for p in (n.get("l1_label", ""), n.get("l2_label", ""), n.get("l3_label", "")) if p)
        lines.append(f"{n['code']} | {path} | {desc}")
    return "\n".join(lines)


def _l2_canon_block(l2_code: str) -> str:
    """某 L2 面向下所有 L3 的判準（canon/allow/forbid + 好壞範例 few-shot）；Stage2b 聚焦複判注入。"""
    lines: list[str] = []
    for n in ai_judge.l3_nodes_for_domains([]):  # 全量後過濾該 L2（節點帶 l2_code）
        if n.get("l2_code") != l2_code:
            continue
        lines.append(
            f"[{n['code']}] {n.get('l3_label') or n.get('l2_label', '')}\n"
            f"  canon: {n.get('canon', '')}\n"
            f"  允許: {'；'.join(n.get('allow', []))}\n"
            f"  禁止: {'；'.join(n.get('forbid', []))}\n"
            f"  好範例: {'；'.join(n.get('positive_cases', []))}\n"
            f"  壞範例: {'；'.join(n.get('negative_cases', []))}"
        )
    return "\n".join(lines)


# prompt caching 友善切分：靜態法典/目錄/格式放 system 前綴（跨筆逐字元相同 → OpenAI 自動前綴快取
# 命中，cached input ~-50%），動態進線文字放 user 末端。順序不可顛倒——caching 靠「最長共同前綴精確
# 比對」，動態內容擺前面會使每筆前綴立即岔開、完全命不中（此為改前 _attr_user 把 text 放最前的 bug）。
def _attr_system(catalog: str) -> str:
    """Stage2 歸因 system prompt：判官指引 + L3 目錄 + 輸出格式（皆靜態，構成可快取前綴）。"""
    return (
        f"{_ATTR_SYS}\n\n"
        f"問題分類 L3 目錄（只能從中選 code）：\n{catalog}\n\n"
        "輸出 JSON：{\"l3_code\":\"最貼切的一條 code（無法歸類回空字串）\","
        "\"confidence\":0~1 浮點,"
        "\"evidence_quote\":\"進線中最能佐證的原文片段\","
        "\"candidates\":[{\"code\":\"code\",\"score\":0~1},...最多3條]}"
    )


def _attr_user(text: str) -> str:
    """Stage2 歸因 user prompt：僅動態進線文字（置末端，維持 system 前綴穩定以命中 prompt caching）。"""
    return f"進線文字：\n{text}"


def _rejudge_system(canon_block: str) -> str:
    """Stage2b 聚焦複判 system prompt：判官指引 + 該 L2 完整厚判準 + 輸出格式（靜態前綴）。"""
    return (
        f"{_ATTR_SYS}\n\n"
        f"候選 L3 完整判準（canon/允許/禁止/正反例）：\n{canon_block}\n\n"
        "依上述判準再判一次，輸出 JSON："
        "{\"l3_code\":\"code\",\"confidence\":0~1,\"evidence_quote\":\"原文片段\"}"
    )


def _rejudge_user(text: str) -> str:
    """Stage2b 複判 user prompt：僅動態進線文字（置末端，維持前綴穩定）。"""
    return f"進線文字：\n{text}"


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
        judgment_stage="insufficient" if polarity == "unknown" else "judged",
        model_used=model,
    )


def _attributed_finding(item: dict, attr: dict, model: str, *, enhanced: bool) -> TicketFinding:
    """負向歸因 finding（Stage2/2b 產出 → TicketFinding）。attr 為淨化後 dict。"""
    conf = attr["confidence"]
    tier = _tier_for(conf)
    stage = _derive_stage("negative", attr["l3_code"], tier, attr.get("evidence_capped", False))
    return TicketFinding(
        **_base_kwargs(item),
        dimension=_dimension_for(attr["l1_domain_code"], attr["l2_label"]),
        recommended_action=_action_for(attr["l1_domain_code"]),
        owner_role=ai_judge.domain_owner(attr["l1_domain_code"]),  # 負責單位（rule _meta.owner_role；未填空）
        problem_summary=attr.get("evidence_quote", "")[:200],
        evidence_quote=attr.get("evidence_quote", ""),
        polarity="negative",
        confidence=conf,
        raw_confidence=attr.get("raw_confidence", conf),
        confidence_tier=tier,
        judgment_stage=stage,
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


def _finalize_attr(item: dict, text: str, out: dict, candidate_codes: frozenset[str]) -> dict:
    """LLM 歸因輸出 → 淨化 attr dict，套 global_rule abstain/evidence 政策（單次 Stage2 與 cascade Stage B 共用）。

    - 負向必給 L1+L2：l3_code 空（模型 abstain）但有候選 → 取 top candidate 回填 L1/L2。
    - L3 abstain：evidence_quote 非原文逐字片段（疑編造）或信心 < l3_min_confidence → L3 降階留空、
      保留 L1+L2、conf 壓至 needs_review 帶交人審（對齊「證據不足不強行判斷」）。
    """
    resolved = _sanitize_l3(str(out.get("l3_code", "")).strip(), candidate_codes)
    cands = [
        {"code": c.get("code", ""), "score": _as_float(c.get("score"))}
        for c in (out.get("candidates") or [])[:3]
        if isinstance(c, dict)
    ]
    if not resolved["l1_domain_code"] and cands:
        top = next((c["code"] for c in cands if c["code"] in candidate_codes), "")
        if top:
            resolved = _sanitize_l3(top, candidate_codes)
    raw_conf = _as_float(out.get("confidence"), 0.5)
    conf = _evidence_cap(resolved["l1_domain_code"], item, raw_conf)
    evidence = str(out.get("evidence_quote", ""))[:300]
    ev = global_rule.evidence_policy()
    pol = global_rule.abstain_policy()
    l3_min = float(ev.get("l3_min_confidence", _tiers().get("jury_low", 0.5)))
    grounded = (not ev.get("require_quote_grounded", True)) or _evidence_grounded(text, evidence)
    l3_abstain_on = pol.get("l3", "allow_empty_low_evidence") == "allow_empty_low_evidence"
    if resolved["l3_code"] and l3_abstain_on and (not grounded or conf < l3_min):
        resolved = {**resolved, "l3_code": "", "l3_label": ""}
        conf = min(conf, l3_min - 0.01)
    return {**resolved, "confidence": conf, "raw_confidence": raw_conf,
            "evidence_quote": evidence, "l3_candidates": cands,
            "evidence_capped": _evidence_capped(resolved["l1_domain_code"], item)}


def _stage2_attribute(item: dict, text: str, model: str) -> dict:
    """Stage2 歸因（負向·單次呼叫全 6 域目錄；cascade 關閉時走此路）→ 淨化後 attr dict。"""
    domains = ai_judge.selectable_domains()
    candidate_codes = frozenset(
        n["code"] for n in ai_judge.l3_nodes_for_domains([d["code"] for d in domains])
    )
    if client.is_stub():  # stub 佔位：未歸類、中信心、待人審
        base = _sanitize_l3("", candidate_codes)
        return {**base, "confidence": 0.5, "raw_confidence": 0.5,
                "evidence_quote": text[:120], "l3_candidates": []}
    out = _call(
        _attr_system(_l3_catalog(domains)), _attr_user(text), "attribute", model,
        schema=_attr_schema(candidate_codes),
    )
    return _finalize_attr(item, text, out, candidate_codes)


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
    out = _call(_rejudge_system(_l2_canon_block(l2_code)), _rejudge_user(text), "rejudge", model, schema=rj_schema)
    new_conf = _as_float(out.get("confidence"), 0.0)
    if new_conf <= prev["confidence"]:
        return prev  # 複判沒更有把握 → 保留原判
    resolved = _sanitize_l3(str(out.get("l3_code", "")).strip(), candidate_codes) if candidate_codes else {}
    merged = {k: v for k, v in resolved.items() if v} if resolved else {}
    conf = _evidence_cap(merged.get("l1_domain_code", prev["l1_domain_code"]), item, new_conf)
    return {**prev, **merged, "confidence": conf, "raw_confidence": new_conf,
            "evidence_quote": str(out.get("evidence_quote", prev.get("evidence_quote", "")))[:300]}


# ── cascade（global_rule.cascade.enabled）：Stage A 域分類 → Stage B 單域 L2/L3 ────────────
def _stage_a_user(text: str) -> str:
    """Stage A user prompt：僅動態進線文字（置末端，維持 system 前綴穩定以命中 prompt caching）。"""
    return f"進線文字：\n{text}"


def _stage_a_system() -> str:
    """Stage A 域分類 system：注入 global_rule.decision_tree（各域 core + 所需證據）+ 跨域界線 + 輸出格式。

    只判 6 域（不灌全部 L3 目錄）→ 候選少、prompt 小且逐字元固定（可快取），對抗一次攤平數十條 L3 的注意力稀釋。
    """
    dt = global_rule.decision_tree()
    gate_lines = "\n".join(
        f"- {g.get('domain', '')}：{g.get('core', '')}（需證據：{g.get('need', '')}）"
        for g in dt.get("gates", [])
    )
    bound_lines = "\n".join(f"- {b}" for b in global_rule.global_boundaries())
    return (
        "你是 KKday 旅遊商品客訴『歸因域分類器』。這是負向評論，依決策樹選出唯一最貼切的歸因域 domain。\n"
        "決策樹（依優先序，先排除商品頁問題再往履約／現場／客服／客人）：\n" + gate_lines + "\n"
        "跨域界線：\n" + bound_lines + "\n"
        "只能從上列 domain 機器值選一個；負向必歸一域、不得空。輸出 JSON："
        "{\"domain\":\"域機器值\",\"confidence\":0~1 浮點,\"evidence_quote\":\"最能佐證的原文片段\"}"
    )


def _stage_a_schema(domain_values: list[str]) -> dict:
    """Stage A 輸出 schema：domain enum 限 6 域機器值（不含空 → 強制選一域）。"""
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["domain", "confidence", "evidence_quote"],
        "properties": {
            "domain": {"type": "string", "enum": sorted(domain_values)},
            "confidence": {"type": "number"},
            "evidence_quote": {"type": "string"},
        },
    }


def _stage_a_domain(text: str, model: str) -> str:
    """Stage A：判 L1 歸因域（machine value）；負向強制選一域；可選 self-consistency 多數決。"""
    domain_values = [d["code"] for d in ai_judge.selectable_domains()]
    if not domain_values:
        return ""
    casc = global_rule.cascade().get("stageA_l1", {})
    sa_model = casc.get("model") or _stage1_model(model)  # 域分類用便宜模型（nano），沿 config stage1_model
    n = max(1, int(casc.get("self_consistency", 1) or 1))
    schema = _stage_a_schema(domain_values)
    votes: list[str] = []
    for _ in range(n):
        out = _call(_stage_a_system(), _stage_a_user(text), "domain", sa_model, schema=schema)
        d = str(out.get("domain", "")).strip()
        if d in domain_values:
            votes.append(d)
    if not votes:
        return domain_values[0]  # 逃生：負向必歸一域（不 abstain L1）
    return Counter(votes).most_common(1)[0][0]


def _stage_b(item: dict, text: str, domain_code: str, model: str) -> dict:
    """Stage B：只注入選中域的 L2/L3 canon，強制選 leaf（保證負向 ≥ L1+L2）+ L3 evidence-gate → attr dict。"""
    nodes = ai_judge.l3_nodes_for_domains([domain_code]) if domain_code else []
    candidate_codes = frozenset(n["code"] for n in nodes)
    if not candidate_codes:  # 該域無節點（逃生）：僅回域層 L1，L2/L3 空
        return {"l1_domain_code": domain_code, "l1_label": ai_judge.domain_label(domain_code),
                "l2_code": "", "l2_label": "", "l3_code": "", "l3_label": "",
                "confidence": 0.4, "raw_confidence": 0.4, "evidence_quote": text[:120], "l3_candidates": []}
    if client.is_stub():  # stub：給該域首個 leaf（負向必 L1+L2）
        base = _sanitize_l3(sorted(candidate_codes)[0], candidate_codes)
        return {**base, "confidence": 0.5, "raw_confidence": 0.5,
                "evidence_quote": text[:120], "l3_candidates": []}
    out = _call(
        _attr_system(_l3_catalog([{"code": domain_code}])), _attr_user(text), "attribute_b", model,
        schema=_attr_schema(candidate_codes, allow_empty=False),  # 強制選 leaf → 保證 ≥ L2
    )
    return _finalize_attr(item, text, out, candidate_codes)


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

    # Stage 2：歸因（負向）——cascade 開啟走 Stage A 域→Stage B 單域 L2/L3（省 token、少候選提準）；
    # 否則走現行單次全目錄（可 feature-flag 回退）。stub 一律走單次路（其自帶 stub 佔位）。
    if global_rule.cascade().get("enabled", False) and not client.is_stub():
        domain = _stage_a_domain(text, model)
        attr = _stage_b(item, text, domain, model)
        return _attributed_finding(item, attr, used_model, enhanced=True)

    attr = _stage2_attribute(item, text, model)

    # Stage 2b：自適應複判（僅 jury 帶且開啟）
    if _prejudge_cfg().get("enable_adaptive_rejudge", True) and _in_jury_band(attr["confidence"]):
        enhanced = _stage2b_rejudge(item, text, attr, model)
        if enhanced is not attr:
            return _attributed_finding(item, enhanced, used_model, enhanced=True)

    return _attributed_finding(item, attr, used_model, enhanced=False)


# ── 多歸因（全 5 來源 1:N）：一則負向評論同時違反多規則 → 多條 attr dict，由 to_findings 各組一 TicketFinding ──
# 復用純函式 _stage_b/_finalize_attr/_action_for/_tier_for/_derive_stage；to_finding（單歸因）保留供其他呼叫端。
def _max_attributions() -> int:
    """一則評論最多輸出幾條獨立違規歸因（config；防過度歸因，硬上限 3、下限 1）。"""
    n = int(_prejudge_cfg().get("max_attributions", 2) or 2)
    return max(1, min(n, 3))


def _attr_schema_multi(candidate_codes: frozenset[str], max_n: int) -> dict:
    """Stage2 多歸因輸出 schema：attributions 陣列（≤max_n），每條一個候選白名單 l3_code（含空 abstain）。"""
    l3_enum = [*sorted(candidate_codes), ""]
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["attributions"],
        "properties": {
            "attributions": {
                "type": "array",
                "maxItems": max_n,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["l3_code", "confidence", "evidence_quote"],
                    "properties": {
                        "l3_code": {"type": "string", "enum": l3_enum},
                        "confidence": {"type": "number"},
                        "evidence_quote": {"type": "string"},
                    },
                },
            }
        },
    }


def _attr_system_multi(catalog: str, max_n: int) -> str:
    """Stage2 多歸因 system prompt：判官指引 + L3 目錄 + 多條輸出格式（靜態前綴，命中 prompt caching）。"""
    return (
        f"{_ATTR_SYS}\n\n"
        f"問題分類 L3 目錄（只能從中選 code）：\n{catalog}\n\n"
        f"這則負向評論可能同時違反多條規則。列出所有『明確且互相獨立』的問題歸因（最多 {max_n} 條，"
        "每條一個 code）：不同問題各歸一條、同一問題勿拆多條、勿為湊數硬加、寧缺勿濫；無法歸類回空陣列。\n"
        "輸出 JSON：{\"attributions\":[{\"l3_code\":\"code\",\"confidence\":0~1 浮點,"
        "\"evidence_quote\":\"進線中最能佐證的原文片段\"},...]}"
    )


def _stage2_attribute_multi(item: dict, text: str, model: str, max_n: int) -> list[dict]:
    """Stage2 多歸因（負向·單次呼叫全域目錄吐多條）→ 淨化後 attr dict 清單（各條逐一過 _finalize_attr）。"""
    domains = ai_judge.selectable_domains()
    candidate_codes = frozenset(
        n["code"] for n in ai_judge.l3_nodes_for_domains([d["code"] for d in domains])
    )
    out = _call(
        _attr_system_multi(_l3_catalog(domains), max_n), _attr_user(text), "attribute", model,
        schema=_attr_schema_multi(candidate_codes, max_n),
    )
    return [
        _finalize_attr(item, text, a, candidate_codes)
        for a in (out.get("attributions") or [])[:max_n]
        if isinstance(a, dict)
    ]


def _stage_a_schema_multi(domain_values: list[str], max_n: int) -> dict:
    """Stage A 多域 schema：domains 陣列（≤max_n），元素限 6 域機器值。"""
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["domains"],
        "properties": {
            "domains": {
                "type": "array",
                "maxItems": max_n,
                "items": {"type": "string", "enum": sorted(domain_values)},
            }
        },
    }


def _stage_a_system_multi(max_n: int) -> str:
    """Stage A 多域分類 system：決策樹 + 跨域界線 + 多域輸出格式（gate/bound 建法同 _stage_a_system）。"""
    dt = global_rule.decision_tree()
    gate_lines = "\n".join(
        f"- {g.get('domain', '')}：{g.get('core', '')}（需證據：{g.get('need', '')}）"
        for g in dt.get("gates", [])
    )
    bound_lines = "\n".join(f"- {b}" for b in global_rule.global_boundaries())
    return (
        "你是 KKday 旅遊商品客訴『歸因域分類器』。這是負向評論，可能同時涉及多個歸因域。\n"
        "決策樹（依優先序，先排除商品頁問題再往履約／現場／客服／客人）：\n" + gate_lines + "\n"
        "跨域界線：\n" + bound_lines + "\n"
        f"列出所有『明確涉及』的 domain（最多 {max_n} 個、各不重複、勿湊數）；負向至少一個、不得空。"
        "輸出 JSON：{\"domains\":[\"域機器值\",...]}"
    )


def _stage_a_domains_multi(text: str, model: str, max_n: int) -> list[str]:
    """Stage A 多域：判涉及的多個 L1 歸因域（machine value）；負向強制 ≥1 域；multi 模式停用 self-consistency。"""
    domain_values = [d["code"] for d in ai_judge.selectable_domains()]
    if not domain_values:
        return []
    casc = global_rule.cascade().get("stageA_l1", {})
    sa_model = casc.get("model") or _stage1_model(model)  # 域分類用便宜模型（nano）
    out = _call(
        _stage_a_system_multi(max_n), _stage_a_user(text), "domain", sa_model,
        schema=_stage_a_schema_multi(domain_values, max_n),
    )
    seen: set[str] = set()
    doms: list[str] = []
    for d in out.get("domains") or []:
        d = str(d).strip()
        if d in domain_values and d not in seen:
            seen.add(d)
            doms.append(d)
    return doms[:max_n] if doms else [domain_values[0]]  # 逃生：負向必歸 ≥1 域


def _resolve_attrs_multi(item: dict, text: str, model: str, max_n: int) -> list[dict]:
    """負向評論 → 多條淨化 attr dict：cascade 開走 Stage A 多域→逐域 Stage B；否則單次多歸因。

    去重（同 L1 域保留信心最高，因 action/owner 為域級）+ 過濾全 abstain（無域）+ 依 confidence 降冪 + cap。
    """
    if client.is_stub():  # stub 無法真歸因：回空（負向但無違規線 → pending_data）
        return []
    if global_rule.cascade().get("enabled", False):
        attrs = [_stage_b(item, text, d, model) for d in _stage_a_domains_multi(text, model, max_n)]
    else:
        attrs = _stage2_attribute_multi(item, text, model, max_n)
    by_domain: dict[str, dict] = {}
    for a in attrs:
        dom = a.get("l1_domain_code", "")
        if not dom:
            continue  # 全 abstain（無域）→ 不成一條違規線
        if dom not in by_domain or a.get("confidence", 0.0) > by_domain[dom].get("confidence", 0.0):
            by_domain[dom] = a
    ranked = sorted(by_domain.values(), key=lambda a: a.get("confidence", 0.0), reverse=True)
    return ranked[:max_n]


def to_findings(item: dict, *, model: str) -> list[TicketFinding]:
    """一條進線 → **多條獨立 TicketFinding**（1:N；一個問題可判出多條歸因分類，各自獨立一筆）。

    全 5 來源統一入口，取代單歸因 to_finding。每條歸因＝一個 TicketFinding（獨立 finding_id、
    L1-L3、信心、分層、判決階段、action），落庫為 judgments 獨立列（見 db.replace_item_findings）。
    - 正向/中性/純好評 → [單一 non_issue finding]（不歸因）。
    - 負向且有歸因 → 每域一條 finding（信心最高標 is_primary）。
    - 負向但全無法歸類 → [單一負向未歸因 finding]（pending_data）。

    finding_id：非負向/未歸因＝`fd_{item_id}`；多歸因每條＝`fd_{item_id}__{l1_domain}`（域級去重→唯一）。

    Args:
        item: 進線列 dict（intake_items / product_reviews 欄；已 _normalize_raw）。
        model: 主判決模型；stub 走啟發式極性、負向回未歸因單筆。

    Returns:
        TicketFinding 清單（≥1 筆）。
    """
    used_model = "stub" if client.is_stub() else model
    text = _text_of(item)
    item_id = item.get("item_id", "")

    if _skip0(item, text):
        return [_non_issue_finding(item, "positive", "heuristic")]
    polarity = _stage1_polarity(item, text, model)
    if polarity != "negative":
        return [_non_issue_finding(item, polarity, used_model)]

    attrs = _resolve_attrs_multi(item, text, model, _max_attributions())
    if not attrs:  # 負向但全無法歸類 → 單筆未歸因（pending_data）
        f = _non_issue_finding(item, "negative", used_model)
        f.judgment_stage = "pending_data"
        f.confidence_tier = "needs_review"
        f.needs_review = True
        f.evidence_quote = text[:200]
        return [f]
    findings: list[TicketFinding] = []
    for i, attr in enumerate(attrs):  # attrs 已依 confidence 降冪、同域去重
        f = _attributed_finding(item, attr, used_model, enhanced=False)
        f.finding_id = f"fd_{item_id}__{attr['l1_domain_code']}"  # 每域一筆獨立列（域級唯一）
        f.is_primary = i == 0  # 信心最高一條為主歸因
        findings.append(f)
    return findings

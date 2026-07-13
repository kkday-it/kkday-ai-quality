"""初判歸因單條引擎：一條進線資料 → TicketFinding（極性閘門 → 歸因）。

**Prompt-as-Source（唯一引擎）**：判決 prompt 唯一真相源＝docs/prompts/prompts/*.md（DB active 版可
線上熱編，見 prompt_source）。Stage1 極性吃 00_polarity（`_pack_polarity`）；歸因段六支域 prompt
（01_C-1~06_C-6）ThreadPool 並行，各判本域問題（`_attrs_pack`）→合流過共用閘門
（`_resolve_attrs_multi` 尾段）。舊「JSON 規則樹拼 system + 單呼叫 32 面向目錄」legacy 引擎與 DB
`judge_rule_versions` C-1~C-6 樹已全數退役（判準與結構皆已轉為 prompt 本身，見 ai_judge.py）。

管線：
- Stage 0 零 LLM 略過純好評（rating=5 + 評論極短 + 無負向詞）→ $0，不歸因。
- Stage 1 極性閘門：進歸因傾向由 judgment.json polarity_gate.attribute_when 決定（預設 negative+neutral
  ——混合中性評論的問題點也歸因）；不在清單者 non_issue 收尾。
- 歸因合流閘門（_resolve_attrs_multi 尾段）：同域去重（保信心最高）+ 排序 + attr_min/secondary_min。
- G1 自動確認路由、證據封頂、grounding 壓信心、stub 雙防線等 code-side 機制不屬 prompt。

finding 為純歸因（軸A）：polarity + L1/L2/L3 + confidence + recommended_action；verdict（軸B）已自
schema.TicketFinding 移除，本引擎不產 verdict，recommended_action 由歸因域推導。
無 token（client.is_stub）→ 全程啟發式，model_used="stub"，讓零 key 也跑通閉環。

⚠️ 凍結件：`_use_config`/`_sample_hit`/`_ensemble_attrs`（跨廠 ensemble voter）與獨立於本次
Prompt-as-Source 重構，本期維持凍結（`to_findings` 內未實際呼叫，voter_cfgs 恆不觸發）——
非 JSON 規則樹遺留，是另一個待重新設計降本方案後才重接的正交機制，不與本檔案其餘刪除範圍混淆。
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, get_args

from app.core import ai_judge
from app.core.schema import CONTENT_DIMENSIONS, RecommendedAction, TicketFinding
from app.judge import ensemble
from app.judge.llm import client

# ── config（judgment.json：信心閾值 + prejudge 旋鈕）；lazy 快取 ──────────────
_cfg_cache: dict | None = None


def _cfg() -> dict:
    """讀 judgment 判決配置（信心閾值 + prejudge 旋鈕）；lazy 快取。

    走 `_shared.read_judgment_config`（DB active 'judgment' 版優先、缺版本回退 seed 檔——judgment 讀取
    單一入口）。存檔後由 rules._reload_judge_cache 呼叫 reload() 清快取即時生效。
    """
    global _cfg_cache
    if _cfg_cache is None:
        from app.core.db import _shared

        _cfg_cache = _shared.read_judgment_config()
    return _cfg_cache


def reload() -> None:
    """清 judgment 配置快取（規則管理存檔後呼叫，使新閾值 / 旋鈕即時反映於初判）。"""
    global _cfg_cache
    _cfg_cache = None


def _tiers() -> dict:
    """判決信心閾值（走 OpenFeature 標準介面 flags.threshold 避免鎖定；provider 讀 judgment DB active
    confidence_tiers、module 級快取，Phase 7 換 Flagsmith 呼叫端零改）。"""
    from app.core import flags

    return {
        "auto_accept": flags.threshold("auto_accept", 0.8),
        "jury_low": flags.threshold("jury_low", 0.5),
        "jury_high": flags.threshold("jury_high", 0.7),
    }


def _prejudge_cfg() -> dict:
    return _cfg().get("prejudge", {})


def _evidence_policy() -> dict:
    """證據政策（judgment.json evidence_policy：attr_min_confidence / secondary_min_confidence /
    require_quote_grounded；2026-07-13 併入原 global_rule.json，減少判決 config 檔案數）。"""
    return _cfg().get("evidence_policy", {})


def _polarity_gate_cfg() -> dict:
    """極性閘門（judgment.json polarity_gate：哪些整體傾向進歸因；同上併入 judgment.json）。"""
    return _cfg().get("polarity_gate", {})


def _stage_effort(key: str) -> str | None:
    """讀 judgment.json prejudge 的 per-stage reasoning_effort 旋鈕（None＝沿用主 config·零行為改變）。

    成本病灶實測（2026-07-06 llm_usage：3716 筆全量重判 ~$21，reasoning tokens 佔總費 ~62%）——
    gpt-5 系列預設 reasoning effort（medium）在極性三分類 / 域選擇這類窄任務上大量空轉。
    QC 可分段調降（"minimal"/"low"）省 completion；降 effort 可能傷準確度，須先以 A/B 驗準再調。
    """
    v = _prejudge_cfg().get(key)
    return str(v) if v else None


def _attr_effort() -> str | None:
    """attribute（Stage2/Stage B 歸因）階段 reasoning_effort override。"""
    return _stage_effort("attribute_reasoning_effort")


def _polarity_effort() -> str | None:
    """polarity（Stage1 極性閘門）階段 reasoning_effort override（三分類窄任務，降檔空間最大）。"""
    return _stage_effort("polarity_reasoning_effort")


def _auto_confirm_cfg() -> dict:
    """G1 自動確認路由旋鈕（judgment.json auto_confirm；DB active 可熱更新）。"""
    return _cfg().get("auto_confirm", {"enabled": True, "audit_sample_rate": 0.05})


def _route_status(tier: str, stage: str) -> str:
    """自動確認路由（G1）：判決結果 → 人工佇列狀態。

    auto_accept + judged（高信心已判定）→ `auto_confirmed`（自動採信、不進人工佇列）；每
    audit_sample_rate 比例抽樣回 `new` 交人工複核（防自動化偏誤：研究指 LLM 高召回低精確，全自動易漏錯）。
    其餘（jury / needs_review / 未定論階段）→ `new`（需人工/覆核）。停用時一律 new（回退舊行為）。
    """
    ac = _auto_confirm_cfg()
    if ac.get("enabled", True) and tier == "auto_accept" and stage == "judged":
        rate = float(ac.get("audit_sample_rate", 0.05) or 0.0)
        if rate > 0 and random.random() < rate:
            return "new"  # 抽樣回人工審核
        return "auto_confirmed"
    return "new"


def _route(findings: list[TicketFinding]) -> list[TicketFinding]:
    """對整組 finding 套用 G1 自動確認路由（依各自 tier+stage 設 status）。就地改並回傳同清單。"""
    for f in findings:
        f.status = _route_status(f.confidence_tier, f.judgment_stage)
    return findings


# 歸因域 → 建議行動 SSOT＝config/ai_judge/domains.json 的 action（ai_judge.domain_action 讀）；
# 不再於此硬編碼（舊 dict 用已廢域名 order/platform/cs，現行 quality/platform/service 查無而失準）。
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


def _evidence_capped(l1_domain: str, item: dict) -> bool:
    """是否因缺外部佐證觸發證據封頂（現行：供應商域缺 order_oid）。

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
    """判決階段派生（歸因 finding 專用；non_issue 於 _non_issue_finding 直接設 stage）。

    歸因列不分整體傾向（負向或混合中性的問題面向同規則）：
    - judged 已判決：歸到 L3+高信心+未觸 cap。
    - pending_data 待數據補充：L3 空(abstain) 或 evidence-cap 觸發(缺訂單/商品頁)——需外部佐證、能救。
    - pending_review 待覆核：有 L3+信心不足(jury/needs_review)+未觸 cap——有候選、靠人審。
    """
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


def _call(
    system: str,
    user: str,
    stage: str,
    model: str,
    *,
    schema: dict | None = None,
    effort: str | None = None,
) -> dict:
    """呼叫 LLM；暫時覆寫 contextvar 的 model（及可選 reasoning_effort）為本階段值，呼叫後還原（thread-local 安全）。

    schema 傳入時走 Structured Outputs（強制 l3_code 只吐候選白名單合法 code）。
    effort 傳入時暫時覆寫 reasoning_effort（① 收緊輸出：attribute 階段可獨立降 effort 省 completion；
    見 _attr_effort。None＝沿用當前 config，零行為改變）。
    """
    from app.core import settings as app_settings

    cur = app_settings.current()
    override: dict = {}
    if model and model != cur.get("model"):
        override["model"] = model
    if effort:
        override["reasoning_effort"] = effort
    if override:
        app_settings.set_current({**cur, **override})
        try:
            return client.chat_json(system, user, stage, schema=schema, cache_key=stage)
        finally:
            app_settings.set_current(cur)
    return client.chat_json(system, user, stage, schema=schema, cache_key=stage)


def _evidence_grounded(text: str, quote: str) -> bool:
    """LLM 回的 evidence_quote 是否確為原文片段（防編造證據）。

    正規化去空白後 substring 比對；片段過短（<4 字）視為未有效佐證。用於「證據不足 → 不自動採信」。
    """
    q = re.sub(r"\s+", "", quote or "")
    t = re.sub(r"\s+", "", text or "")
    return len(q) >= 4 and q in t


# ── stub 啟發式（無 token 時零 key 跑通閉環；佔位非真值）─────────────────────
def _stub_polarity(item: dict, text: str) -> tuple[str, int]:
    """rating + 負向關鍵詞 啟發式極性（stub）；回 (polarity, sentiment 1-5)。

    sentiment 取 rating 細分並夾區間：rating≤2→負向(1-2)、≥4→正向(4-5)、中間看負向詞；
    無法判別一律兜底中立 3（傾向只有 positive/negative/neutral 三態）。
    """
    r = item.get("rating")
    if isinstance(r, int):
        if r <= 2:
            return "negative", max(1, min(2, r))  # rating 1→1、2→2
        if r >= 4:
            return "positive", min(5, r)  # rating 4→4、5→5
    if _has_neg_kw(text):
        return "negative", 1
    return "neutral", 3


# ── 解析與淨化 ──────────────────────────────────────────────────────────────
def _as_float(v: Any, default: float = 0.0) -> float:
    """寬鬆轉 float（LLM 可能回字串）；失敗回 default，夾到 [0,1]。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, f))


# ── finding 組裝 ────────────────────────────────────────────────────────────
def _base_kwargs(item: dict) -> dict:
    """TicketFinding 共用簿記欄（id/來源/時間/oid）。ticket_id 存特徵 id（source_id），供 db 落 judgments.source_id。"""
    source = item.get("source", "")
    source_id = item.get("source_id", "")
    now = _now_iso()
    return {
        "finding_id": f"fd_{source}_{source_id}",  # 冪等：重判整組替換（見 db.replace_source_findings）
        "ticket_id": source_id,  # ＝特徵 id（product_reviews→rec_oid…）
        "prod_oid": item.get("prod_oid", "") or "",
        "pkg_oid": item.get("pkg_oid", "") or "",
        "order_oid": item.get("order_oid", "") or "",
        "status": "new",
        "created_at": now,
        "judged_at": now,
    }


def _non_issue_finding(item: dict, polarity: str, model: str, sentiment: int = 0) -> TicketFinding:
    """正向/中性 → 不歸 L1-L3、不進問題清單（純非問題）。sentiment＝情緒分 1-5（0＝未判）。"""
    return TicketFinding(
        **_base_kwargs(item),
        dimension="non_content",
        recommended_action="no_action",
        polarity=polarity,
        sentiment_score=sentiment,
        confidence=1.0,
        raw_confidence=1.0,
        confidence_tier="auto_accept",
        judgment_stage="judged",
        model_used=model,
    )


def _attributed_finding(
    item: dict,
    attr: dict,
    model: str,
    *,
    enhanced: bool,
    polarity: str = "negative",
    sentiment: int = 0,
) -> TicketFinding:
    """歸因 finding（Stage2/2b 產出 → TicketFinding）。attr 為淨化後 dict。

    polarity＝整則評論傾向（列上可辨識歸因來自負向或混合中性評論）；歸因面向本身必為問題點。
    sentiment＝整則情緒分 1-5（與 polarity 同段判；0＝未帶）。
    """
    conf = attr["confidence"]
    tier = _tier_for(conf)
    # 判決落點：prompt_pack 只判到 L2（l3_code 恆空字串，見 _sanitize_l2），L2 面向即為本階段終點
    # （不得因 l3_code 空就誤標 pending_data——高信心 L2 歸因照走 judged/G1 路由）。
    landing = attr["l3_code"] or attr["l2_code"]
    stage = _derive_stage(polarity, landing, tier, attr.get("evidence_capped", False))
    return TicketFinding(
        **_base_kwargs(item),
        dimension=_dimension_for(attr["l1_domain_code"], attr["l2_label"]),
        recommended_action=_action_for(attr["l1_domain_code"]),
        # 語系→摘要 map；LLM 未產則回退原文片段包成 {zh-tw: …}（表格恆有顯示值）
        summary=attr.get("summary")
        or ({"zh-tw": attr["evidence_quote"][:200]} if attr.get("evidence_quote") else {}),
        evidence_quote=attr.get("evidence_quote", ""),
        polarity=polarity,
        sentiment_score=sentiment,
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
        isinstance(item.get("rating"), int)
        and item.get("rating") >= cfg.get("stage0_min_rating", 5)
        and len(text) <= cfg.get("stage0_max_comment_len", 8)
        and not _has_neg_kw(text)
    )


def _clamp_sentiment(raw: Any, polarity: str) -> int:
    """LLM 情緒分正規化為 1-5，並夾進 polarity 對應區間確保與傾向一致（負面 1-2 / 中立 3 / 正面 4-5）。

    區間鉗制：保證我方 polarity（驅動歸因）與 sentiment 不矛盾，同時保留正/負向內 1-2、4-5 的細分；
    中立恆為 3（doc 定義單點）。raw 缺失/非法時取該區間預設中值。
    """
    try:
        v = int(round(float(raw)))
    except (TypeError, ValueError):
        v = 0
    if polarity == "positive":
        return min(5, max(4, v)) if v else 5
    if polarity == "negative":
        return min(2, max(1, v)) if v else 1
    return 3  # neutral（含非法 polarity 兜底：中立恆 3）


def _stage1_polarity(item: dict, text: str, main_model: str) -> tuple[str, int]:
    """Stage1 極性閘門：回 (polarity, sentiment 1-5)。委派 `_pack_polarity`（吃 00_polarity 的
    System/User/Schema，Prompt-as-Source 唯一真相源）；獨立留名供 to_findings 呼叫與測試 monkeypatch。
    """
    return _pack_polarity(item, text, main_model)


def _summary_map(raw) -> dict[str, str]:
    """LLM summary 陣列 [{lang,text}] → 語系→簡明摘要 map（去重·每條 ≤200 字·確保含 zh-tw）。

    容錯：raw 為字串（舊格式/單語）→ 當作 zh-tw；空/異常→空 map。表格顯示只取 zh-tw。
    """
    if isinstance(raw, str):
        s = raw.strip()[:200]
        return {"zh-tw": s} if s else {}
    out: dict[str, str] = {}
    for it in raw or []:
        if not isinstance(it, dict):
            continue
        lang = str(it.get("lang", "")).strip().lower()
        text = str(it.get("text", "")).strip()[:200]
        if lang and text and lang not in out:
            out[lang] = text
    if out and "zh-tw" not in out:  # LLM 漏標 zh-tw → 取第一條當顯示版，保證表格有值
        out["zh-tw"] = next(iter(out.values()))
    return out


# ── 多歸因（全 5 來源 1:N）：一則負向評論同時違反多規則 → 多條 attr dict，由 to_findings 各組一 TicketFinding ──
def _max_attributions() -> int:
    """一則評論最多輸出幾條獨立違規歸因（config；防過度歸因，硬上限 3、下限 1）。"""
    n = int(_prejudge_cfg().get("max_attributions", 2) or 2)
    return max(1, min(n, 3))


def _gate_attrs(attrs: list[dict], max_n: int) -> list[dict]:
    """歸因合流尾段共用閘門：同域去重（信心最高）+ 過濾全 abstain + attr_min/secondary_min 信心閘門 + 排序 + cap。

    純函式（與產生來源解耦——`_resolve_attrs_multi` 餵 `_attrs_pack` 產出、Prompt 評測診斷路徑可餵
    自己的診斷 attrs），確保「這條歸因會不會被判決採信」的規則只有一份，不因生產/評測兩條路徑
    各自實作而 drift。
    """
    amin = _as_float(_evidence_policy().get("attr_min_confidence"), 0.0)
    # attr 級最低信心閘門（config evidence_policy.attr_min_confidence；0＝關）：
    # 殺「強制/湊數」型歸因（實測 conf 0.09~0.12 的目錄第一組殭屍列）——信心低到這種程度
    # 代表模型自己都不信，留著只汙染列表與統計；正常弱信心（≥閘門）仍留給人審分層。
    by_domain: dict[str, dict] = {}
    for a in attrs:
        dom = a.get("l1_domain_code", "")
        if not dom:
            continue  # 全 abstain（無域）→ 不成一條違規線
        if amin and a.get("confidence", 0.0) < amin:
            continue  # 低於 attr 閘門 → 整條丟棄（視同棄權）
        if dom not in by_domain or a.get("confidence", 0.0) > by_domain[dom].get("confidence", 0.0):
            by_domain[dom] = a
    ranked = sorted(by_domain.values(), key=lambda a: a.get("confidence", 0.0), reverse=True)
    # 次要歸因信心閘門（config evidence_policy.secondary_min_confidence；0＝關）：多歸因時
    # 非 primary 條目要求更高信心——低信心第二歸因（實測 conf 0.49~0.65 的「順帶一提」面向）
    # 是與多模型多數決不一致的 extra 簇主因；primary 不受影響，仍走 attr_min_confidence。
    smin = _as_float(_evidence_policy().get("secondary_min_confidence"), 0.0)
    if smin and len(ranked) > 1:
        ranked = [ranked[0]] + [a for a in ranked[1:] if a.get("confidence", 0.0) >= smin]
    return ranked[:max_n]


def _resolve_attrs_multi(
    item: dict, text: str, model: str, max_n: int, polarity: str = "negative"
) -> list[dict]:
    """負向/混合中性評論 → 多條淨化 attr dict：六域並行歸因（`_attrs_pack`）→ 合流尾段共用閘門（`_gate_attrs`）。"""
    if client.is_stub():  # stub 無法真歸因：回空（負向但無違規線 → pending_data）
        return []
    attrs = _attrs_pack(item, text, model, max_n, polarity)
    return _gate_attrs(attrs, max_n)


@contextmanager
def _use_config(cfg: dict):
    """暫時把整套 effective LLM config 設為 current（ensemble voter 換廠：base_url/token/model 全換）。

    有別於 _call 只覆寫 model 一欄——跨廠 voter（OpenAI↔Gemini↔ByteDance）需連 base_url/token 一起換。
    離開時還原原 config，不污染後續判決。
    """
    from app.core import settings as app_settings

    cur = app_settings.current()
    app_settings.set_current(cfg)
    try:
        yield
    finally:
        app_settings.set_current(cur)


def _sample_hit(item: dict, rate: float) -> bool:
    """deterministic 抽樣命中：以 source_id hash 落在 [0, rate) 判定（可重現·同筆每次一致，非亂數）。

    用於「④抽樣稽核」——對高信心筆按比例也跑 ensemble 驗證，補 confidence-gate 只驗低信心的盲區
    （防自動化偏誤：LLM 高召回低精確，高信心也可能系統性錯）。rate≤0 全不中、≥1 全中。
    """
    if rate <= 0:
        return False
    if rate >= 1:
        return True
    key = f"{item.get('source', '')}_{item.get('source_id', '')}"
    h = int(hashlib.md5(key.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return h < rate


def _ensemble_attrs(
    item: dict,
    text: str,
    base_attrs: list[dict],
    base_model: str,
    voter_cfgs: list[dict],
    sample_rate: float = 0.0,
    polarity: str = "negative",
) -> tuple[list[dict], list[dict]]:
    """confidence-gated 聯合判決：低信心 or 抽樣命中才跨廠複判 → merge_votes；否則原樣回（省 token）。

    觸發條件（任一）：① 主判決有低信心 attr（< auto_accept）→ 該投票釐清；② 命中抽樣（sample_rate）→
    高信心筆的品質稽核（與 gate 互補：gate 驗不確定、抽樣驗確定）。兩者皆否 → 回原 attrs + 空票（省 token）。
    觸發後對每個 voter 換整套 config 重跑 _resolve_attrs_multi，連同主判決 L1 域投票合併；全分歧丟棄 →
    保底回原 attrs（不因 ensemble 丟失判決）。

    Args:
        item/text: 進線列與其文字。
        base_attrs: 主判決（base_model）的多歸因 attr 清單。
        base_model: 主判決模型名。
        voter_cfgs: 跨廠 voter 的 effective LLM config 清單（各含 model/base_url/token）。
        sample_rate: 高信心筆的抽樣稽核比例（0＝純 confidence-gate·1＝全量 ensemble）。

    Returns:
        (聯合後 attrs, model_votes 攤平票)；未觸發 ensemble 時 model_votes 為空。
    """
    auto = _tiers().get("auto_accept", 0.8)
    low = any(ensemble.should_ensemble(a.get("confidence", 0.0), auto) for a in base_attrs)
    if not low and not _sample_hit(item, sample_rate):
        return base_attrs, []  # 全高信心且未命中抽樣 → 不 ensemble（省 token）
    max_n = _max_attributions()
    voter_results = [{"model": base_model, "attrs": base_attrs}]
    for cfg in voter_cfgs:
        with _use_config(cfg):
            v_attrs = _resolve_attrs_multi(item, text, cfg.get("model", ""), max_n, polarity)
        voter_results.append({"model": cfg.get("model", ""), "attrs": v_attrs})
    merged = ensemble.merge_votes(voter_results)
    return (merged["merged"] or base_attrs), merged["model_votes"]


def to_findings(
    item: dict,
    *,
    model: str,
    voter_cfgs: list[dict] | None = None,
    ensemble_sample_rate: float = 0.0,
) -> list[TicketFinding]:
    """一條進線 → **多條獨立 TicketFinding**（1:N；一個問題可判出多條歸因分類，各自獨立一筆）。

    全 5 來源統一入口。每條歸因＝一個 TicketFinding（獨立 finding_id、L1-L3、信心、分層、判決階段、
    action），落庫為 judgments 獨立列（見 db.replace_source_findings）。
    進歸因的傾向由 judgment.json polarity_gate.attribute_when 決定（預設 negative+neutral——
    混合中性評論的具體問題點也要歸因，kiki 2026-07-06 反饋）：
    - 正向/純好評/不在 gate 清單 → [單一 non_issue finding]（不歸因）。
    - 負向/混合中性且有歸因 → 每域一條 finding（信心最高標 is_primary；列 polarity＝整則傾向）。
    - 負向但全無法歸類 → [單一負向未歸因 finding]（pending_data）。
    - 混合中性但找不到具體問題點 → [單一 non_issue finding]（judged，非 pending_data——整體無礙）。

    finding_id：非負向/未歸因＝`fd_{item_id}`；多歸因每條＝`fd_{item_id}__{l1_domain}`（域級去重→唯一）。

    Args:
        item: 進線列 dict（intake_items / product_reviews 欄；已 _normalize_raw）。
        model: 主判決模型；stub 走啟發式極性、負向回未歸因單筆。
        voter_cfgs: ⚠️ 本期未接線（凍結中，見模組 docstring）——參數保留供 API 相容，不觸發 ensemble。
        ensemble_sample_rate: ⚠️ 同上，本期未接線。

    Returns:
        TicketFinding 清單（≥1 筆）；model_votes 本期恆空（ensemble 未接線）。
    """
    used_model = "stub" if client.is_stub() else model
    text = _text_of(item)
    src = item.get("source", "")
    source_id = item.get("source_id", "")

    # 各 return 皆過 _route：依 finding 的 tier+stage 設 status（G1 自動確認路由）。
    if _skip0(item, text):
        # skip0＝高星短好評（rating≥5）→ 正向、情緒分 5
        return _route([_non_issue_finding(item, "positive", "heuristic", sentiment=5)])
    polarity, sentiment = _stage1_polarity(item, text, model)
    if polarity not in _attribute_when():  # config 驅動（judgment.json polarity_gate）
        return _route([_non_issue_finding(item, polarity, used_model, sentiment=sentiment)])

    attrs = _resolve_attrs_multi(item, text, model, _max_attributions(), polarity)
    # confidence-gated ensemble：本期仍凍結未接線（見模組 docstring）——voter 複判需對六域並行
    # prompt_pack 各廠都跑一輪，成本乘數過大；roadmap 設計降本方案後再呼叫 _ensemble_attrs 重接。
    # voter_cfgs/ensemble_sample_rate 參數保留（API 相容 prejudge_batch/v1/judgment 呼叫端），本期不使用。
    model_votes: list[dict] = []
    if not attrs:
        if (
            polarity != "negative"
        ):  # 混合中性但未找到具體問題點 → 純 non_issue（整體無礙，無需補數據）
            return _route([_non_issue_finding(item, polarity, used_model, sentiment=sentiment)])
        f = _non_issue_finding(
            item, "negative", used_model, sentiment=sentiment
        )  # 負向但全無法歸類 → 單筆未歸因（pending_data）
        f.judgment_stage = "pending_data"
        f.confidence_tier = "needs_review"
        f.needs_review = True
        f.evidence_quote = text[:200]
        return _route([f])
    findings: list[TicketFinding] = []
    ensemble_model = "ensemble" if model_votes else used_model  # ensemble 觸發 → model 標 ensemble
    for i, attr in enumerate(attrs):  # attrs 已依 confidence 降冪、同域去重
        f = _attributed_finding(
            item, attr, ensemble_model, enhanced=False, polarity=polarity, sentiment=sentiment
        )
        f.finding_id = (
            f"fd_{src}_{source_id}__{attr['l1_domain_code']}"  # 每域一筆獨立列（域級唯一）
        )
        f.is_primary = i == 0  # 信心最高一條為主歸因
        f.model_votes = model_votes  # ensemble 各 voter 攤平票（單模型判決為空）
        findings.append(f)
    return _route(findings)


def _proposed_label_path(proposed_code: str) -> str:
    """真值 code → 可讀「L1 › L2 › L3」路徑（供 LLM prompt）；未知 code 回原 code。

    proposed_code 可為 L1 域 code（如 content）或任一層 C-code（級聯選出）。走 ai_judge.path_label
    （級聯樹建立時登記的完整路徑 label，任一層皆解得），未登記者回原 code。
    """
    return ai_judge.path_label(proposed_code) or proposed_code


def score_true_label(text: str, proposed_code: str, model: str) -> dict:
    """LLM 評估『人工提議真值分類』與反饋原文的契合信心（標真值把關，不改變 AI 判決）。

    給定反饋全文 + 人工用級聯選出的歸因分類，請 LLM 評 0~1 契合度 + 一句理由。呼叫端據此與原判信心對比，
    信心明顯下降時要求填修改理由（防亂標／故意修改）。stub 模式無法真評分 → 回中性 0.5。

    Args:
        text: 反饋原文（_text_of 產出）。
        proposed_code: 人工提議的真值分類 code（L1 域 code 或 L3 葉 C-code）。
        model: 評分用模型（呼叫端由 judgment.true_label.evaluate_model / stage1_model 決定）。

    Returns:
        {confidence: 0~1 float, reason: 一句話理由}。
    """
    if client.is_stub():
        return {"confidence": 0.5, "reason": "stub 模式：未接 LLM，無法真評分"}
    label = _proposed_label_path(proposed_code)
    system = (
        "你是內容品質稽核員。判斷『人工提議的歸因分類』是否正確反映這則使用者反饋。"
        "只評契合度、不改判決。confidence 高＝該分類確實貼合反饋內容；低＝不貼合／證據不足／過度延伸。"
        '輸出 JSON：{"confidence":0~1 浮點,"reason":"一句話中文理由"}。'
    )
    user = f"反饋內容：\n{text}\n\n人工提議的歸因分類：{label}（code={proposed_code}）"
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["confidence", "reason"],
        "properties": {
            "confidence": {"type": "number"},
            "reason": {"type": "string"},
        },
    }
    out = _call(system, user, "true_label", model, schema=schema)
    conf = max(0.0, min(1.0, _as_float(out.get("confidence"), 0.5)))
    return {"confidence": conf, "reason": str(out.get("reason", ""))[:200]}


def _attribute_when() -> frozenset[str]:
    """哪些整體傾向進歸因（judgment.json polarity_gate.attribute_when；SSOT＝該靜態檔）。

    容錯：字串（legacy attribute_only_when 單值）與清單皆收；只認 negative/neutral（positive
    恆 non_issue，防 config 誤填放行好評歸因）；config 缺失/全無效 → 回退 {"negative"}（保守舊行為）。
    """
    gate = _polarity_gate_cfg()
    raw = gate.get("attribute_when") or gate.get("attribute_only_when") or []
    vals = [raw] if isinstance(raw, str) else list(raw or [])
    allowed = frozenset(
        v for v in (str(x).strip().lower() for x in vals) if v in ("negative", "neutral")
    )
    return allowed or frozenset({"negative"})


def batch_service_tier(n_items: int) -> str | None:
    """批次判決的 serving tier（judgment.json prejudge.batch_service_tier；None＝標準）。

    "flex"＝OpenAI flex processing：計價 -50%（Batch 同級）換變動延遲，適合背景批次；
    小批次（< flex_min_items，如單筆重判＝使用者在等結果）不套 flex 保互動延遲。
    僅 OpenAI provider 生效（client 端依 base_url 反推守門），429 資源不足自動回退標準 tier。

    Args:
        n_items: 本批標的筆數（小於門檻不套 flex）。

    Returns:
        tier 字串（"flex"）或 None（沿用標準）。
    """
    cfg = _prejudge_cfg()
    tier = cfg.get("batch_service_tier")
    if not tier:
        return None
    if n_items < int(cfg.get("flex_min_items", 10) or 0):
        return None
    return str(tier)


# ── L2 面向淨化（`_attrs_pack` 六域並行歸因共用）────────────────────────────
# 初判只依評論文字判到 L1+L2 即收手（L3 細項常缺商品/訂單佐證而不可靠，本期不判）。
def _l2_label_map() -> dict[str, tuple[str, str, str]]:
    """L2 面向 code → (l1_domain, l1_label, l2_label)（自攤平葉推導；含 L2 葉自身）。"""
    out: dict[str, tuple[str, str, str]] = {}
    for n in ai_judge.l3_nodes_for_domains([]):
        c = str(n.get("l2_code") or "")
        if c and c not in out:
            out[c] = (n.get("l1_domain", ""), n.get("l1_label", ""), n.get("l2_label", ""))
    return out


def _sanitize_l2(code: str, valid: dict[str, tuple[str, str, str]]) -> dict[str, str]:
    """校驗 l2_code ∈ 面向白名單並回填 l1/l2 label；非法回全空（未歸類）。L3 恆空（留待深判）。"""
    info = valid.get(code)
    if not info:
        return {
            k: ""
            for k in ("l1_domain_code", "l1_label", "l2_code", "l2_label", "l3_code", "l3_label")
        }
    l1, l1_label, l2_label = info
    return {
        "l1_domain_code": l1,
        "l1_label": l1_label,
        "l2_code": code,
        "l2_label": l2_label,
        "l3_code": "",
        "l3_label": "",
    }


def _finalize_attr_l2(
    item: dict, text: str, out: dict, valid: dict[str, tuple[str, str, str]]
) -> dict:
    """L2 歸因輸出 → 淨化 attr dict：白名單校驗 + evidence grounding + 證據封頂（與 L3 版同政策）。

    grounding 不落地（evidence_quote 非原文逐字片段）→ 信心壓到 needs_review 帶交人審——
    「低信心反饋」的第一環（第二環＝attr_min_confidence 閘門整條丟棄、第三環＝負反饋重問）。
    """
    resolved = _sanitize_l2(str(out.get("l2_code", "")).strip(), valid)
    raw_conf = _as_float(out.get("confidence"), 0.5)
    conf = _evidence_cap(resolved["l1_domain_code"], item, raw_conf)
    evidence = str(out.get("evidence_quote", ""))[:300]
    summary = _summary_map(out.get("summary"))
    ev = _evidence_policy()
    grounded = (not ev.get("require_quote_grounded", True)) or _evidence_grounded(text, evidence)
    if resolved["l2_code"] and not grounded:
        conf = min(
            conf, _tiers().get("jury_low", 0.5) - 0.01
        )  # 證據不落地 → 壓入人審帶（不強行採信）
    return {
        **resolved,
        "confidence": conf,
        "raw_confidence": raw_conf,
        "summary": summary,
        "evidence_quote": evidence,
        "l3_candidates": [],
        "evidence_capped": _evidence_capped(resolved["l1_domain_code"], item),
    }


# ── Prompt-as-Source 引擎：極性 + 六域並行歸因 ────────────────────────────
# 判決 prompt 唯一真相源＝docs/prompts/prompts/*.md（DB active 版可線上熱編，見 prompt_source）。
def _render_pack_user(template: str, text: str, polarity: str) -> str:
    """填 prompt user 模板槽位（{TEXT}/{POLARITY}）。

    用 replace 而非 str.format——md 未來若在 user 節放 JSON 範例，裸大括號會令 format() 拋錯；
    replace 只換明確槽位、對其他字元零副作用。
    """
    return template.replace("{TEXT}", text).replace("{POLARITY}", polarity)


def _pack_polarity(item: dict, text: str, main_model: str) -> tuple[str, int]:
    """Stage1 極性：吃 00_polarity 的 System/User/Schema（唯一真相源）。

    stub 走啟發式；_clamp_sentiment 保留為 code-side 保險（保證 sentiment 與 polarity 區間一致，
    即使 prompt schema 未強約束）。
    """
    if client.is_stub():
        return _stub_polarity(item, text)
    from app.judge import prompt_source

    p = prompt_source.load(prompt_source.POLARITY_ID)
    out = _call(
        p["system"],
        _render_pack_user(p["user_template"], text, ""),  # polarity 無 {POLARITY} 槽
        "polarity",
        _stage1_model(main_model),
        schema=p["schema"],
        effort=_polarity_effort(),
    )
    pol = str(out.get("polarity", "")).strip().lower()
    pol = pol if pol in ("positive", "negative", "neutral") else "neutral"
    return pol, _clamp_sentiment(out.get("sentiment"), pol)


def _attrs_pack(
    item: dict, text: str, model: str, max_n: int, polarity: str = "negative"
) -> list[dict]:
    """六域並行歸因：各域 prompt 獨立判本域問題 → 合流淨化 attr dict 清單。

    六支域 prompt（01_C-1~06_C-6）ThreadPool 並行，各 chat_json(System, User.填槽, schema=檔內 schema)；
    每域回 {"attributions":[{l2_code,confidence,summary,evidence_quote}...]}，逐條過 _finalize_attr_l2
    （grounding 壓信心 / 證據封頂 / 白名單校驗）。l2→l1 由 _sanitize_l2 映射——自洽 drift 護欄
    （prompt_source.validate：facet_catalog codes == Schema l2_code enum）保證回的 l2_code 必落該
    prompt 對應域，故等同「由回覆 prompt 歸屬直接給」。合流後的同域去重 / 排序 / attr_min /
    secondary_min 閘門由 _resolve_attrs_multi 尾段共用。

    並行安全：contextvar _current（effective LLM 設定）於呼叫端 copy_context() 快照後 ctx.run——比照
    prejudge_batch 配方（同一 Context 不可並發 run，故每域一份獨立快照）。
    """
    if client.is_stub():  # stub 無法真歸因：回空（負向但無違規線 → to_findings 產 pending_data）
        return []
    from concurrent.futures import ThreadPoolExecutor
    from contextvars import copy_context

    from app.judge import prompt_source

    valid = _l2_label_map()
    effort = _attr_effort()
    pids = prompt_source.DOMAIN_PROMPT_IDS

    def _one(pid: str) -> list[dict]:
        p = prompt_source.load(pid)
        out = _call(
            p["system"],
            _render_pack_user(p["user_template"], text, polarity),
            "attribute",
            model,
            schema=p["schema"],
            effort=effort,
        )
        return [
            _finalize_attr_l2(item, text, a, valid)
            for a in (out.get("attributions") or [])[:max_n]
            if isinstance(a, dict)
        ]

    # 呼叫端快照 context（帶 effective 設定）；每域一份（Context 不可並發 run）。fail-loud：單域呼叫
    # 拋錯即整條 item 失敗（比照 legacy 單呼叫），交批次層 item-level 重試，不靜默吞掉漏一個域。
    ctxs = [copy_context() for _ in pids]
    attrs: list[dict] = []
    with ThreadPoolExecutor(max_workers=len(pids)) as ex:
        futures = [ex.submit(ctx.run, _one, pid) for ctx, pid in zip(ctxs, pids, strict=True)]
        for fut in futures:
            attrs.extend(fut.result())
    return attrs

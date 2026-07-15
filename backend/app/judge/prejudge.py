"""初判歸因單條引擎：一條進線資料 → TicketFinding（極性閘門 → 歸因）。

**Prompt-as-Source（唯一引擎）**：判決 prompt 唯一真相源＝prompts/*.md（DB active 版可
線上熱編，見 prompt_source）。Stage1 極性吃 00_polarity（`_pack_polarity`）；歸因段六支域 prompt
（01_C-1~06_C-6）ThreadPool 並行，各判本域問題（`_attrs_pack`）→合流過共用閘門
（`_resolve_attrs_multi` 尾段）。判準與結構皆源自 prompt 本身（見 ai_judge.py）。

管線：
- Stage 0 零 LLM 略過純好評（rating=5 + 評論極短 + 無負向詞）→ $0，不歸因。
- Stage 1 極性閘門：進歸因傾向由 judgment.json polarity_gate.attribute_when 決定（預設 negative+neutral
  ——混合中性評論的問題點也歸因）；不在清單者 non_issue 收尾。
- 歸因合流閘門（_resolve_attrs_multi 尾段）：同(域,面向)去重（保信心最高，同 L1 多 L2 並列）+ 排序 + attr_min/secondary_min。
- G1 自動確認路由、證據封頂、grounding 壓信心、stub 雙防線等 code-side 機制不屬 prompt。

finding 為純歸因（軸A）：polarity + L1/L2/L3 + confidence + recommended_action；verdict（軸B）已自
schema.TicketFinding 移除，本引擎不產 verdict，recommended_action 由歸因域推導。
無 token（client.is_stub）→ 全程啟發式，model_used="stub"，讓零 key 也跑通閉環。
"""

from __future__ import annotations

import json
import random
import re
from datetime import datetime, timezone
from typing import Any, get_args

from app.core import ai_judge
from app.core.schema import RecommendedAction, TicketFinding
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


# 歸因域 → 建議行動 SSOT＝各域 `## Taxonomy` root 的 action（ai_judge.domain_action 讀）；不再於此硬編碼。
_VALID_ACTIONS: frozenset[str] = frozenset(get_args(RecommendedAction))


def _now_iso() -> str:
    """UTC ISO 時間（判決/建立時間戳）。"""
    return datetime.now(timezone.utc).isoformat()


def _text_of(item: dict) -> str:
    """取判決主輸入文字：優先 comment（intake_items）/ content（canonical 主文），回退 raw 內常見文字欄。

    有 canonical title（product_reviews rec_title / freshdesk subject）時前置「標題：」一行——
    標題常單獨承載問題點（標題罵、內文短），一併送判並讓 evidence_quote 可自標題落地。
    """
    txt = (item.get("comment") or item.get("content") or "").strip()
    if not txt:
        raw = item.get("raw") or {}
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (ValueError, TypeError):
                raw = {}
        for k in ("content", "comment", "chatbot_conversation", "human_conversation", "feedback"):
            v = raw.get(k)
            if isinstance(v, str) and v.strip():
                txt = v.strip()
                break
    title = str(item.get("title") or "").strip()
    if title and title not in txt:
        return f"標題：{title}\n{txt}" if txt else f"標題：{title}"
    return txt


def _neg_keywords() -> list[str]:
    return _prejudge_cfg().get("neg_keywords", [])


def _has_neg_kw(text: str) -> bool:
    """文字是否含負向關鍵詞（stub/Stage0 判負向用）。"""
    return any(kw in text for kw in _neg_keywords())


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


def _evidence_gated_domains() -> frozenset[str]:
    """需外部訂單佐證才可高信心的域清單——自各域 `## Taxonomy` root 的 `evidence_gated`（ai_judge 派生）。

    「這域要不要外部佐證」＝該域自己的語義，寫在自己 prompt 的 taxonomy root（取代 judgment.json
    硬編碼 evidence_gated_domains）；要納入他域只改該域 prompt。
    """
    return ai_judge.evidence_gated_domains()


def _evidence_capped(l1_domain: str, item: dict) -> bool:
    """是否因缺外部佐證觸發證據封頂（evidence_gated_domains 內的域缺 order_oid）。

    判決階段派生用此旗標把「需外部資料才能定論」的案子導向 pending_data（待數據補充）。
    """
    has_order = bool(item.get("order_oid") or (item.get("raw") or {}).get("order_oid"))
    return l1_domain in _evidence_gated_domains() and not has_order


def _evidence_cap(l1_domain: str, item: dict, raw_conf: float) -> float:
    """證據封頂：無訂單證據不得高信心判供應商履約（供應商域需訂單佐證）。

    對齊 SSOT「evidence-gated」：進線多為 symptom_only（評論無訂單），故 supplier 域在缺
    order_oid 時封頂至 jury_high 以下，逼入評審/人審而非自動採信。
    """
    if _evidence_capped(l1_domain, item):
        return min(raw_conf, _tiers().get("jury_high", 0.7) - 0.01)
    return raw_conf


def _derive_stage(polarity: str, landing: str, tier: str, evidence_capped: bool) -> str:
    """判決階段派生（歸因 finding 專用；non_issue 於 _non_issue_finding 直接設 stage）。

    歸因列不分整體傾向（負向或混合中性的問題面向同規則）：
    - judged 已判決：歸到 L2+高信心+未觸 cap。
    - pending_data 待數據補充：L2 空(abstain) 或 evidence-cap 觸發(缺訂單/商品頁)——需外部佐證、能救。
    - pending_review 待覆核：有 L2+信心不足(jury/needs_review)+未觸 cap——有候選、靠人審。
    """
    if evidence_capped or not landing:
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
    label: str | None = None,
) -> dict:
    """呼叫 LLM；暫時覆寫 contextvar 的 model（及可選 reasoning_effort）為本階段值，呼叫後還原（thread-local 安全）。

    schema 傳入時走 Structured Outputs（強制 l2_code 只吐候選白名單合法 code）。
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
            return client.chat_json(
                system, user, stage, schema=schema, cache_key=stage, label=label
            )
        finally:
            app_settings.set_current(cur)
    return client.chat_json(system, user, stage, schema=schema, cache_key=stage, label=label)


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
    # 判決落點＝L2 面向（prompt_pack 只判到 L2；高信心 L2 歸因走 judged/G1 路由，空 L2 才 pending_data）。
    landing = attr["l2_code"]
    stage = _derive_stage(polarity, landing, tier, attr.get("evidence_capped", False))
    return TicketFinding(
        **_base_kwargs(item),
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


def _stage1_polarity(
    item: dict,
    text: str,
    main_model: str,
    *,
    versions: dict[str, int] | None = None,
) -> tuple[str, int]:
    """Stage1 極性閘門：回 (polarity, sentiment 1-5)。委派 `_pack_polarity`（吃 00_polarity 的
    System/User/Schema，Prompt-as-Source 唯一真相源）；獨立留名供 to_findings 呼叫與測試 monkeypatch。
    """
    return _pack_polarity(item, text, main_model, versions=versions)


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
    """一則評論最多輸出幾條獨立違規歸因（config；防過度歸因，硬上限 8、下限 1）。

    上限由 3→8：改採「同(域,面向)並列」歸因粒度後（見 `_gate_attrs`），一則反饋可同時命中
    多域＋同域多面向（6 域 × 各域 maxItems 2 ＝ 理論 12），硬上限放寬以容納，實際條數仍由
    config max_attributions 與信心閘門收斂。
    """
    n = int(_prejudge_cfg().get("max_attributions", 2) or 2)
    return max(1, min(n, 8))


def _gate_attrs(attrs: list[dict], max_n: int) -> list[dict]:
    """歸因合流尾段共用閘門：同(域,面向)去重（信心最高）+ 過濾全 abstain + attr_min/secondary_min 信心閘門 + 排序 + cap。

    純函式（與產生來源解耦——`_resolve_attrs_multi` 餵 `_attrs_pack` 產出、Prompt 評測診斷路徑可餵
    自己的診斷 attrs），確保「這條歸因會不會被判決採信」的規則只有一份，不因生產/評測兩條路徑
    各自實作而 drift。

    去重粒度＝(l1_domain, l2_code)：同一 L1 下不同 L2 面向各自成一條獨立歸因（一則反饋常同時
    命中一個域的多個面向，如「服務」域下「導遊素質」＋「注意事項宣導不足」）；僅同(域,面向)重複
    才收斂取信心最高。跨域＋同域多面向的總條數由 max_n（config max_attributions）統一 cap。
    """
    amin = _as_float(_evidence_policy().get("attr_min_confidence"), 0.0)
    # attr 級最低信心閘門（config evidence_policy.attr_min_confidence；0＝關）：
    # 殺「強制/湊數」型歸因（實測 conf 0.09~0.12 的目錄第一組殭屍列）——信心低到這種程度
    # 代表模型自己都不信，留著只汙染列表與統計；正常弱信心（≥閘門）仍留給人審分層。
    by_facet: dict[tuple[str, str], dict] = {}
    for a in attrs:
        dom = a.get("l1_domain_code", "")
        if not dom:
            continue  # 全 abstain（無域）→ 不成一條違規線
        if amin and a.get("confidence", 0.0) < amin:
            continue  # 低於 attr 閘門 → 整條丟棄（視同棄權）
        key = (dom, a.get("l2_code", ""))  # 同(域,面向)才去重；同域不同面向各自保留
        if key not in by_facet or a.get("confidence", 0.0) > by_facet[key].get("confidence", 0.0):
            by_facet[key] = a
    ranked = sorted(by_facet.values(), key=lambda a: a.get("confidence", 0.0), reverse=True)
    # 次要歸因信心閘門（config evidence_policy.secondary_min_confidence；0＝關）：多歸因時
    # 非 primary 條目要求更高信心——低信心第二歸因（實測 conf 0.49~0.65 的「順帶一提」面向）
    # 是與多模型多數決不一致的 extra 簇主因；primary 不受影響，仍走 attr_min_confidence。
    smin = _as_float(_evidence_policy().get("secondary_min_confidence"), 0.0)
    if smin and len(ranked) > 1:
        ranked = [ranked[0]] + [a for a in ranked[1:] if a.get("confidence", 0.0) >= smin]
    return ranked[:max_n]


def _resolve_attrs_multi(
    item: dict,
    text: str,
    model: str,
    max_n: int,
    polarity: str = "negative",
    *,
    versions: dict[str, int] | None = None,
) -> list[dict]:
    """負向/混合中性評論 → 多條淨化 attr dict：六域並行歸因（`_attrs_pack`）→ 合流尾段共用閘門（`_gate_attrs`）。"""
    if client.is_stub():  # stub 無法真歸因：回空（負向但無違規線 → pending_data）
        return []
    attrs = _attrs_pack(item, text, model, max_n, polarity, versions=versions)
    return _gate_attrs(attrs, max_n)


def to_findings(
    item: dict,
    *,
    model: str,
    versions: dict[str, int] | None = None,
) -> list[TicketFinding]:
    """一條進線 → **多條獨立 TicketFinding**（1:N；一個問題可判出多條歸因分類，各自獨立一筆）。

    全 5 來源統一入口。每條歸因＝一個 TicketFinding（獨立 finding_id、L1-L3、信心、分層、判決階段、
    action），落庫為 judgments 獨立列（見 db.replace_source_findings）。
    進歸因的傾向由 judgment.json polarity_gate.attribute_when 決定（預設 negative+neutral——
    混合中性評論的具體問題點也要歸因，kiki 2026-07-06 反饋）：
    - 正向/純好評/不在 gate 清單 → [單一 non_issue finding]（不歸因）。
    - 負向/混合中性且有歸因 → 每(域,面向)一條 finding（信心最高標 is_primary；列 polarity＝整則傾向；
      同一 L1 下多個 L2 面向各自成一條並列，如「服務」域同時命中導遊素質＋注意事項宣導不足）。
    - 負向但全無法歸類 → [單一負向未歸因 finding]（pending_data）。
    - 混合中性但找不到具體問題點 → [單一 non_issue finding]（judged，非 pending_data——整體無礙）。

    finding_id：非負向/未歸因＝`fd_{item_id}`；多歸因每條＝`fd_{item_id}__{l1_domain}__{l2_code}`（面向級去重→唯一）。

    Args:
        item: 進線列 dict（intake_items / product_reviews 欄；已 _normalize_raw）。
        model: 主判決模型；stub 走啟發式極性、負向回未歸因單筆。
        versions: 版本選擇功能（{rule_code: 指定版本號}），透傳給 `prompt_source.load`；不帶時
            行為與既有 production 路徑完全一致（皆讀 DB active）。

    Returns:
        TicketFinding 清單（≥1 筆）。
    """
    used_model = "stub" if client.is_stub() else model
    text = _text_of(item)
    src = item.get("source", "")
    source_id = item.get("source_id", "")

    # 各 return 皆過 _route：依 finding 的 tier+stage 設 status（G1 自動確認路由）。
    if _skip0(item, text):
        # skip0＝高星短好評（rating≥5）→ 正向、情緒分 5
        return _route([_non_issue_finding(item, "positive", "heuristic", sentiment=5)])
    polarity, sentiment = _stage1_polarity(item, text, model, versions=versions)
    if polarity not in _attribute_when():  # config 驅動（judgment.json polarity_gate）
        return _route([_non_issue_finding(item, polarity, used_model, sentiment=sentiment)])

    attrs = _resolve_attrs_multi(
        item, text, model, _max_attributions(), polarity, versions=versions
    )
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
    for i, attr in enumerate(attrs):  # attrs 已依 confidence 降冪、同(域,面向)去重
        f = _attributed_finding(
            item, attr, used_model, enhanced=False, polarity=polarity, sentiment=sentiment
        )
        f.finding_id = (
            # 每(域,面向)一筆獨立列（面向級唯一）——同 L1 下多個 L2 面向並列時 id 不撞、落庫不互相覆蓋。
            f"fd_{src}_{source_id}__{attr['l1_domain_code']}__{attr['l2_code']}"
        )
        f.is_primary = i == 0  # 信心最高一條為主歸因
        findings.append(f)
    return _route(findings)


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


def max_workers_for(model: str) -> int:
    """該 model 的批次併發上限（judgment.json prejudge.max_workers_by_model；缺則 max_workers_default）。

    切旗艦模型（帳號 TPM/RPM tier 通常較低）時自動降併發，避免撞 OpenAI rate limit。此為軟上限，呼叫端
    （prejudge_batch）再與製程級硬天花板 env.prejudge_max_workers 取 min，只能往下收斂、不會超過全域
    Semaphore 容量。手動維護表（不自動查帳號 tier）：切模型前於 judgment.json 更新對應值；缺表或缺該
    model 時回 max_workers_default。

    Args:
        model: 生效模型機器值（如 "gpt-5-mini" / "gpt-5.5"）。

    Returns:
        該 model 的批次併發上限（正整數）。
    """
    cfg = _prejudge_cfg()
    by_model = cfg.get("max_workers_by_model") or {}
    default = int(cfg.get("max_workers_default", 32) or 32)
    return int(by_model.get(model, default) or default)


def _domain_retry() -> int:
    """單域 LLM 呼叫失敗的有界重試次數（judgment.json prejudge.domain_retry，預設 1；0＝關閉）。

    P1 後單域一次呼叫已含 SDK max_retries 指數退避、耗盡才真失敗；此為「其餘域都成功、僅該域瞬時失利」
    情境的止血——省下整筆 6 域重打的浪費。耗盡仍讓整筆 fail-loud（見 _attrs_pack；不改完整性保證）。
    """
    return max(0, int(_prejudge_cfg().get("domain_retry", 1) or 0))


def adaptive_concurrency() -> dict:
    """自適應併發（AIMD）參數（judgment.json prejudge.adaptive_concurrency）；enabled 預設 True。

    ceiling＝`max_workers_for`（呼叫端再 min env 硬天花板）；item 因 429 失敗 → limit*=`backoff`（乘性
    收縮），清空 `probe_interval_s` 秒無 429 → limit+=1（加性回升），不低於 `floor`。關閉即回退固定
    `max_workers`。保證有能力時爬回 ceiling、過載時才降，免手動預測 model×effort×帳號 tier 的最佳併發。
    """
    cfg = _prejudge_cfg().get("adaptive_concurrency") or {}
    return {
        "enabled": bool(cfg.get("enabled", True)),
        "backoff": float(cfg.get("backoff", 0.5) or 0.5),
        "probe_interval_s": float(cfg.get("probe_interval_s", 3.0) or 3.0),
        "floor": int(cfg.get("floor", 2) or 2),
    }


# ── L2 面向淨化（`_attrs_pack` 六域並行歸因共用）────────────────────────────
# 初判只依評論文字判到 L1+L2 即收手（L3 細項常缺商品/訂單佐證而不可靠，本期不判）。
def _l2_label_map() -> dict[str, tuple[str, str, str]]:
    """L2 面向 code → (l1_domain, l1_label, l2_label)（自攤平葉推導；含 L2 葉自身）。"""
    out: dict[str, tuple[str, str, str]] = {}
    for n in ai_judge.l2_nodes_for_domains([]):
        c = str(n.get("l2_code") or "")
        if c and c not in out:
            out[c] = (n.get("l1_domain", ""), n.get("l1_label", ""), n.get("l2_label", ""))
    return out


def _sanitize_l2(code: str, valid: dict[str, tuple[str, str, str]]) -> dict[str, str]:
    """校驗 l2_code ∈ 面向白名單並回填 l1/l2 label；非法回全空（未歸類）。"""
    info = valid.get(code)
    if not info:
        return {k: "" for k in ("l1_domain_code", "l1_label", "l2_code", "l2_label")}
    l1, l1_label, l2_label = info
    return {
        "l1_domain_code": l1,
        "l1_label": l1_label,
        "l2_code": code,
        "l2_label": l2_label,
    }


def _finalize_attr_l2(
    item: dict, text: str, out: dict, valid: dict[str, tuple[str, str, str]]
) -> dict:
    """L2 歸因輸出 → 淨化 attr dict：白名單校驗 + evidence grounding + 證據封頂。

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
        "evidence_capped": _evidence_capped(resolved["l1_domain_code"], item),
    }


# ── Prompt-as-Source 引擎：極性 + 六域並行歸因 ────────────────────────────
# 判決 prompt 唯一真相源＝prompts/*.md（DB active 版可線上熱編，見 prompt_source）。
def _render_pack_user(template: str, text: str, polarity: str) -> str:
    """填 prompt user 模板槽位（{TEXT}/{POLARITY}）。

    用 replace 而非 str.format——md 未來若在 user 節放 JSON 範例，裸大括號會令 format() 拋錯；
    replace 只換明確槽位、對其他字元零副作用。
    """
    return template.replace("{TEXT}", text).replace("{POLARITY}", polarity)


def _pack_polarity(
    item: dict,
    text: str,
    main_model: str,
    *,
    versions: dict[str, int] | None = None,
) -> tuple[str, int]:
    """Stage1 極性：吃 00_polarity 的 System/User/Schema（唯一真相源）。

    stub 走啟發式；_clamp_sentiment 保留為 code-side 保險（保證 sentiment 與 polarity 區間一致，
    即使 prompt schema 未強約束）。

    Args:
        versions: 版本選擇功能（初判分類指定歷史版本），透傳給 `prompt_source.load`。
    """
    if client.is_stub():
        return _stub_polarity(item, text)
    from app.judge import prompt_source

    p = prompt_source.load(prompt_source.POLARITY_ID, versions=versions)
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
    item: dict,
    text: str,
    model: str,
    max_n: int,
    polarity: str = "negative",
    *,
    versions: dict[str, int] | None = None,
) -> list[dict]:
    """六域並行歸因：各域 prompt 獨立判本域問題 → 合流淨化 attr dict 清單。

    六支域 prompt（01_C-1~06_C-6）ThreadPool 並行，各 chat_json(System, User.填槽, schema=檔內 schema)；
    每域回 {"attributions":[{l2_code,confidence,summary,evidence_quote}...]}，逐條過 _finalize_attr_l2
    （grounding 壓信心 / 證據封頂 / 白名單校驗）。l2→l1 由 _sanitize_l2 映射——自洽 drift 護欄
    （Schema l2_code enum 由 `## Taxonomy` 派生）保證回的 l2_code 必落該
    prompt 對應域，故等同「由回覆 prompt 歸屬直接給」。合流後的同(域,面向)去重 / 排序 / attr_min /
    secondary_min 閘門由 _resolve_attrs_multi 尾段共用。

    並行安全：contextvar _current（effective LLM 設定）於呼叫端 copy_context() 快照後 ctx.run——比照
    prejudge_batch 配方（同一 Context 不可並發 run，故每域一份獨立快照）。
    """
    if client.is_stub():  # stub 無法真歸因：回空（負向但無違規線 → to_findings 產 pending_data）
        return []
    import time
    from concurrent.futures import ThreadPoolExecutor
    from contextvars import copy_context

    from app.judge import prompt_source

    valid = _l2_label_map()
    effort = _attr_effort()
    pids = prompt_source.DOMAIN_PROMPT_IDS
    dom_retry = _domain_retry()
    retry_delay = (
        0.5  # 秒；單域重試前的短暫緩衝（SDK 內建退避已在單次呼叫內耗盡，此為再打一次前的間隔）
    )

    def _one(pid: str) -> list[dict]:
        p = prompt_source.load(pid, versions=versions)
        dom = pid.split("_")[1] if "_" in pid else pid  # "01_C-1_content" → "C-1"（日誌分組鍵）
        last_exc: Exception | None = None
        for attempt in range(
            dom_retry + 1
        ):  # 有界重試：僅該域，其餘域不受影響；耗盡仍拋出（fail-loud）
            try:
                out = _call(
                    p["system"],
                    _render_pack_user(p["user_template"], text, polarity),
                    "attribute",
                    model,
                    schema=p["schema"],
                    effort=effort,
                    label=dom,
                )
                return [
                    _finalize_attr_l2(item, text, a, valid)
                    for a in (out.get("attributions") or [])[:max_n]
                    if isinstance(a, dict)
                ]
            except Exception as e:  # noqa: BLE001  單域瞬時失利給有界重試（止血）；耗盡仍拋出維持整筆 fail-loud
                last_exc = e
                if attempt < dom_retry:
                    time.sleep(retry_delay)
        raise last_exc  # type: ignore[misc]  迴圈至少一輪，last_exc 必非 None

    # 呼叫端快照 context（帶 effective 設定）；每域一份（Context 不可並發 run）。fail-loud：單域呼叫
    # 拋錯即整條 item 失敗（比照 legacy 單呼叫），交批次層 item-level 重試，不靜默吞掉漏一個域。
    ctxs = [copy_context() for _ in pids]
    attrs: list[dict] = []
    with ThreadPoolExecutor(max_workers=len(pids)) as ex:
        futures = [ex.submit(ctx.run, _one, pid) for ctx, pid in zip(ctxs, pids, strict=True)]
        for fut in futures:
            attrs.extend(fut.result())
    return attrs

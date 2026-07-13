"""初判歸因單條引擎：一條進線資料 → TicketFinding（極性閘門 → 候選域 canon 聚焦歸因）。

accuracy/token 最優管線（真實 83k 筆精算，見 plans/toasty-churning-shell.md）：
- Stage 0 零 LLM 略過純好評（rating=5 + 評論極短 + 無負向詞）→ $0，不歸因。
- Stage 1 極性閘門（便宜模型 / stub 啟發式）：進歸因傾向由 global_rule.polarity_gate.attribute_when
  決定（預設 negative+neutral——混合中性評論的問題點也歸因）；不在清單者 non_issue 收尾。
- Stage 2 歸因（負向/混合中性；單次呼叫 + 候選域 L3 catalog 聚焦）：選 l3_code + 信心。
- Stage 2b 自適應複判（僅信心落 jury 帶）：注入該 L2 完整 canon 再判一次，取信心較高者。

判準來源一律 core/ai_judge（rule_C-*.json 的 canon/allow/forbid/好壞範例），禁在此自寫判準。
finding 為純歸因（軸A）：polarity + L1/L2/L3 + confidence + recommended_action；verdict（軸B）已自
schema.TicketFinding 移除，本引擎不產 verdict，recommended_action 由歸因域推導。
無 token（client.is_stub）→ 全程啟發式，model_used="stub"，讓零 key 也跑通閉環。
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, get_args

from app.core import ai_judge, global_rule
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


def _stage_a_effort() -> str | None:
    """Stage A（cascade 域/面向分類）階段 reasoning_effort override。"""
    return _stage_effort("stage_a_reasoning_effort")


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
        "required": ["l3_code", "confidence", "summary", "evidence_quote", "candidates"],
        "properties": {
            "l3_code": {"type": "string", "enum": l3_enum},
            "confidence": {"type": "number"},
            # 反饋摘要：語系→簡明摘要陣列（1~3 條·去重）；務必含一條 lang='zh-tw'（台灣繁體·簡明扼要總結到位）
            "summary": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["lang", "text"],
                    "properties": {"lang": {"type": "string"}, "text": {"type": "string"}},
                },
            },
            "evidence_quote": {"type": "string"},  # 逐字原文佐證（grounding + 佐證欄）
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
# 判官提示詞（極性/歸因角色 + 判斷流程）唯一 SSOT＝global_rule（config/ai_judge/global_rule.json，
# 規則配置頁「global 整體規則」可編輯）；**code 不留政策複本**——loader 缺 DB 版時自動回退 seed 檔
# （global_rule.json，同在整體配置內）。下方 `or "…"` 僅為「config 全缺才防空 prompt」的極簡緊急字串，
# 非政策內容，避免與 config 雙份 drift。域界線/正反例走 _domain_boundaries（ai_judge L1 canon）、
# L3 canon 走 _l3_catalog——全部動態自 rule tree（曾踩坑：判官只認寫死那份，QC 改 UI 卻不生效）。
def _polarity_sys() -> str:
    """極性判官提示詞（SSOT＝global_rule.polarity_guidance）；config 全缺時回極簡緊急字串防空 prompt。"""
    return global_rule.polarity_guidance() or "你是進線極性判官，只判整體傾向、不歸因，輸出 JSON。"


def _attr_sys() -> str:
    """歸因判官提示詞（SSOT＝global_rule.attribution_guidance，規則配置頁可編輯）；域界線/正反例另由
    _domain_boundaries 動態拼接；config 全缺時回極簡緊急字串防空 prompt。"""
    return (
        global_rule.attribution_guidance()
        or "你是客訴歸因判官，依下方六域界線鐵則與 L3 目錄選最貼切 code，輸出 JSON。"
    )


def _l3_catalog(domains: list[dict], *, rich: bool = False, only_l2: str = "") -> str:
    """候選域的 L3 目錄（code | 域›面向›細項 | 判準）；Stage2/Stage B 注入。

    rich=False（flat 全域目錄）：每葉只印完整 canon——84+ 葉時再多欄位會稀釋注意力（實測病灶）。
    rich=True（cascade Stage B 單域）：葉數少（≤20），額外注入每葉 forbid（禁止界線）與
    negative_cases（常見誤判→改歸哪條），並以 L2 canon 作面向分組標題——喚醒 rule tree 厚判準
    （原本 90 葉 allow/forbid/正反例 100% 填滿卻零注入，QC 編輯無效）。canon 完整注入不截斷
    （原 [:40] 曾砍掉尾段「不得…」界線導致誤判）；皆屬靜態前綴，prompt caching 攤平長度成本。
    only_l2：僅列該 L2 面向下的葉（stage_a_level=l1l2 時 Stage B 候選縮到選中面向）。
    """
    lines: list[str] = []
    codes = [d["code"] for d in domains]
    seen_l2: set[str] = set()
    nodes = ai_judge.l3_nodes_for_domains(codes)
    if only_l2:
        nodes = [n for n in nodes if n.get("l2_code") == only_l2]
    for n in nodes:
        if rich:
            l2_code = str(n.get("l2_code") or "")
            if l2_code and l2_code not in seen_l2:  # L2 面向標題（canon 非空才印）
                seen_l2.add(l2_code)
                l2j = ai_judge.l2_judgment(l2_code)
                l2_canon = (l2j.get("canon") or "").strip()
                if l2_canon:
                    lines.append(f"— {n.get('l2_label', '')}（{l2_code}）：{l2_canon}")
        desc = (n.get("canon") or "").strip()
        # 變深度：路徑動態串接（L2 葉無 l3_label → 只印 域›面向），省略空層
        path = "›".join(
            p for p in (n.get("l1_label", ""), n.get("l2_label", ""), n.get("l3_label", "")) if p
        )
        lines.append(f"{n['code']} | {path} | {desc}")
        if rich:
            forbid = [f for f in (n.get("forbid") or []) if f]
            neg = [x for x in (n.get("negative_cases") or []) if x]
            if forbid:
                lines.append("　⛔不得：" + "；".join(forbid))
            if neg:
                lines.append("　❌誤判例：" + "；".join(neg))
    return "\n".join(lines)


def _domain_boundaries() -> str:
    """六域界線塊：各域 L1 canon + forbid（鄰域路由），組成「先判域」的域層界線。

    修誤判根因：判官原本只吃 _ATTR_SYS 通用文字 + 每葉 canon 一行，rule tree 的 L1 域判準
    （canon「改頁面文案就能解決」+ forbid「抱怨東西實體品質差→C-2」「頁面有寫客人沒看→C-6-6」等
    鄰域排除）從未進 prompt，導致非內容評論大量誤落商品內容。此處把 ai_judge.l1_judgment 的域界線
    注入 active 單次 prompt（免啟 cascade）。內容隨 DB active rule 版本穩定，屬靜態前綴、命中 caching。
    """
    lines: list[str] = []
    for d in ai_judge.selectable_domains():
        j = ai_judge.l1_judgment(d["code"])
        canon = (j.get("canon") or "").strip()
        if not canon:
            continue
        lines.append(f"【{d['label']}】{canon}")
        # 正例（屬本域）/ 反例（常見誤判→改歸他域）/ 禁止界線：皆取自 rule tree（可 QC 編輯），非寫死於 _ATTR_SYS
        pos = [p for p in (j.get("positive_cases") or []) if p]
        neg = [n for n in (j.get("negative_cases") or []) if n]
        forbid = [f for f in (j.get("forbid") or []) if f]
        if pos:
            lines.append("　✅屬本域：" + "；".join(pos))
        if neg:
            lines.append("　❌禁歸本域（常見誤判）：" + "；".join(neg))
        if forbid:
            lines.append("　⛔不得：" + "；".join(forbid))
    return "\n".join(lines)


# prompt caching 友善切分：靜態法典/目錄/格式放 system 前綴（跨筆逐字元相同 → OpenAI 自動前綴快取
# 命中，cached input ~-50%），動態進線文字放 user 末端。順序不可顛倒——caching 靠「最長共同前綴精確
# 比對」，動態內容擺前面會使每筆前綴立即岔開、完全命不中（此為改前 _attr_user 把 text 放最前的 bug）。
def _attr_system(catalog: str) -> str:
    """Stage2 歸因 system prompt：判官指引 + L3 目錄 + 輸出格式（皆靜態，構成可快取前綴）。"""
    return (
        f"{_attr_sys()}\n\n"
        f"六域界線鐵則（先判屬哪個域，再選細項 code）：\n{_domain_boundaries()}\n\n"
        f"問題分類 L3 目錄（只能從中選 code）：\n{catalog}\n\n"
        '輸出 JSON：{"l3_code":"最貼切的一條 code（無法歸類回空字串）",'
        '"confidence":0~1 浮點,'
        '"summary":[{"lang":"語言碼","text":"該語言的簡明摘要"},...]（1~3 條·去重）——'
        '務必含一條 lang="zh-tw"（台灣繁體中文書面語，一句話簡明扼要、總結到位，不論原文何種語言）；'
        "若原文非繁中，另附一條 lang=原文語言碼（如 ja/ko/en/th）的簡明摘要；每條 text 都要簡明扼要，"
        '"evidence_quote":"進線中最能佐證的原文片段（保留原文語言，逐字不改寫）",'
        '"candidates":[{"code":"code","score":0~1},...最多3條]}'
    )


def _attr_user(text: str) -> str:
    """Stage2 歸因 user prompt：僅動態進線文字（置末端，維持 system 前綴穩定以命中 prompt caching）。"""
    return f"進線文字：\n{text}"


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
    return {
        k: "" for k in ("l1_domain_code", "l1_label", "l2_code", "l2_label", "l3_code", "l3_label")
    }


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
    # 判決落點：l3 深度＝L3 葉（空＝abstain→pending_data）；l2 深度＝L2 面向即為本階段終點
    # （L3 本來就留白待深判，不得因此誤標 pending_data——高信心 L2 歸因照走 judged/G1 路由）。
    landing = attr["l3_code"] or (attr["l2_code"] if global_rule.prejudge_depth() == "l2" else "")
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
    """Stage1 極性閘門：回 (polarity, sentiment 1-5)。LLM 同段輸出細分情緒分，夾進 polarity 區間。"""
    if client.is_stub():
        return _stub_polarity(item, text)
    out = _call(
        _polarity_sys(),
        f'文字：\n{text}\n\n輸出 JSON：{{"polarity":"positive|negative|neutral","sentiment":1-5}}',
        "polarity",
        _stage1_model(main_model),
        effort=_polarity_effort(),
    )
    pol = str(out.get("polarity", "")).strip().lower()
    # 非法輸出兜底中立（傾向只有三態；Structured Outputs 下極少觸發）
    pol = pol if pol in ("positive", "negative", "neutral") else "neutral"
    return pol, _clamp_sentiment(out.get("sentiment"), pol)


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
    summary = _summary_map(out.get("summary"))  # 語系→簡明摘要 map（去重·含 zh-tw）
    ev = global_rule.evidence_policy()
    pol = global_rule.abstain_policy()
    l3_min = float(ev.get("l3_min_confidence", _tiers().get("jury_low", 0.5)))
    grounded = (not ev.get("require_quote_grounded", True)) or _evidence_grounded(text, evidence)
    l3_abstain_on = pol.get("l3", "allow_empty_low_evidence") == "allow_empty_low_evidence"
    if resolved["l3_code"] and l3_abstain_on and (not grounded or conf < l3_min):
        resolved = {**resolved, "l3_code": "", "l3_label": ""}
        conf = min(conf, l3_min - 0.01)
    return {
        **resolved,
        "confidence": conf,
        "raw_confidence": raw_conf,
        "summary": summary,
        "evidence_quote": evidence,
        "l3_candidates": cands,
        "evidence_capped": _evidence_capped(resolved["l1_domain_code"], item),
    }


# ── cascade（global_rule.cascade.enabled）：Stage A 多域 → Stage B 逐域 L2/L3（見 _resolve_attrs_multi）──
def _stage_a_user(text: str) -> str:
    """Stage A user prompt：僅動態進線文字（置末端，維持 system 前綴穩定以命中 prompt caching）。"""
    return f"進線文字：\n{text}"


def _stage_b(item: dict, text: str, domain_code: str, model: str, l2_code: str = "") -> dict:
    """Stage B：只注入選中範圍的 L2/L3 canon，選 leaf（可棄權）+ L3 evidence-gate → attr dict。

    allow_empty=True（與 flat 路徑語義一致）：Stage A 選錯域時，域內沒有貼合葉可回空棄權→
    該域整條丟棄（_sanitize_l3('') 全空 → _resolve_attrs_multi 過濾）。原 allow_empty=False
    強制選葉會湊數——實測湊向目錄第一組（C-2-1 網路品質）產生 conf 0.09 殭屍歸因（primacy bias）。
    負向評論若全部域都棄權 → to_findings 既有「未歸因 pending_data」兜底（誠實優於捏造）。
    l2_code（stage_a_level=l1l2）：候選葉縮到該 L2 面向下（Stage A 已選到面向，聚焦更小目錄）。
    """
    nodes = ai_judge.l3_nodes_for_domains([domain_code]) if domain_code else []
    if l2_code:
        nodes = [n for n in nodes if n.get("l2_code") == l2_code]
    candidate_codes = frozenset(n["code"] for n in nodes)
    if not candidate_codes:  # 該域無節點（逃生）：僅回域層 L1，L2/L3 空
        return {
            "l1_domain_code": domain_code,
            "l1_label": ai_judge.domain_label(domain_code),
            "l2_code": "",
            "l2_label": "",
            "l3_code": "",
            "l3_label": "",
            "confidence": 0.4,
            "raw_confidence": 0.4,
            "evidence_quote": text[:120],
            "l3_candidates": [],
            "evidence_capped": _evidence_capped(domain_code, item),
        }  # 對齊正常路徑，缺外部佐證仍封頂
    if client.is_stub():  # stub：給該域首個 leaf（負向必 L1+L2）
        base = _sanitize_l3(sorted(candidate_codes)[0], candidate_codes)
        return {
            **base,
            "confidence": 0.5,
            "raw_confidence": 0.5,
            "evidence_quote": text[:120],
            "l3_candidates": [],
        }
    sb_model = (
        global_rule.cascade().get("stageB", {}).get("model") or model
    )  # config 覆寫，空＝沿用主模型
    out = _call(
        # rich：單域葉數少，注入 L3 forbid/誤判例 + L2 canon 標題（喚醒厚判準；flat 全目錄不 rich）
        _attr_system(_l3_catalog([{"code": domain_code}], rich=True, only_l2=l2_code)),
        _attr_user(text),
        "attribute_b",
        sb_model,
        schema=_attr_schema(
            candidate_codes, allow_empty=True
        ),  # 可棄權：域內無貼合葉回空（寧缺勿濫）
        effort=_attr_effort(),
    )
    return _finalize_attr(item, text, out, candidate_codes)


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


def _attr_system_multi(catalog: str, max_n: int, polarity: str = "negative") -> str:
    """Stage2 多歸因 system prompt：判官指引 + L3 目錄 + 多條輸出格式（靜態前綴，命中 prompt caching）。

    polarity 決定收尾指令：負向＝可能違反多條規則；混合中性＝只歸因具體問題點、稱讚面向不歸因。
    """
    if polarity == "negative":
        lead = f"這則負向評論可能同時違反多條規則。列出所有『明確且互相獨立』的問題歸因（最多 {max_n} 條，"
    else:
        lead = (
            "這是混合傾向評論（整體滿意但提到具體問題點）：只歸因其中的『具體問題點』，"
            f"被稱讚/滿意的面向不歸因。列出問題歸因（最多 {max_n} 條，"
        )
    return (
        f"{_attr_sys()}\n\n"
        f"六域界線鐵則（先判屬哪個域，再選細項 code）：\n{_domain_boundaries()}\n\n"
        f"問題分類 L3 目錄（只能從中選 code）：\n{catalog}\n\n"
        + lead
        + "每條一個 code）：不同問題各歸一條、同一問題勿拆多條、勿為湊數硬加、寧缺勿濫；無法歸類回空陣列。\n"
        '輸出 JSON：{"attributions":[{"l3_code":"code","confidence":0~1 浮點,'
        '"evidence_quote":"進線中最能佐證的原文片段"},...]}'
    )


def _stage2_attribute_multi(
    item: dict, text: str, model: str, max_n: int, polarity: str = "negative"
) -> list[dict]:
    """Stage2 多歸因（單次呼叫全域目錄吐多條）→ 淨化後 attr dict 清單（各條逐一過 _finalize_attr）。"""
    domains = ai_judge.selectable_domains()
    candidate_codes = frozenset(
        n["code"] for n in ai_judge.l3_nodes_for_domains([d["code"] for d in domains])
    )
    out = _call(
        _attr_system_multi(_l3_catalog(domains), max_n, polarity),
        _attr_user(text),
        "attribute",
        model,
        schema=_attr_schema_multi(candidate_codes, max_n),
        effort=_attr_effort(),
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


def _stage_a_system_multi(
    max_n: int, polarity: str = "negative", excluded_labels: tuple[str, ...] = ()
) -> str:
    """Stage A 多域分類 system：六域界線鐵則（＝_domain_boundaries，L1 canon+正反例）+ 多域輸出格式。

    界線＝_domain_boundaries（ai_judge L1 canon，與單次 Stage2 同一 SSOT）；舊 global_rule
    decision_tree.gates / global_boundaries 平行 SSOT 已於 2026-07-07 自 config 移除（曾致
    「改了 gates 以為生效、判決其實走 L1 canon」的 drift）。
    polarity 決定收尾指令：負向強制 ≥1 域；混合中性只列有具體問題證據的域、可回空（防好評硬湊歸因）。
    excluded_labels（低信心重路由）存在時，負向也放寬可回空——排除後可能確無他域可歸，強制反而再湊數。
    """
    if polarity == "negative" and not excluded_labels:
        head = "這是負向評論，可能同時涉及多個歸因域。"
        tail = f"列出所有『明確涉及』的 domain（最多 {max_n} 個、各不重複、勿湊數）；負向至少一個、不得空。"
    elif polarity == "negative":
        head = "這是負向評論，可能同時涉及多個歸因域。"
        tail = f"列出『明確涉及』的 domain（最多 {max_n} 個、各不重複、勿湊數）；確無合適域則回空陣列。"
    else:
        head = "這是混合傾向評論（整體滿意但提到具體問題點），只針對其中的『問題面向』分類。"
        tail = (
            f"只列出『有具體問題證據』的 domain（最多 {max_n} 個、各不重複）；"
            "被稱讚/滿意的面向不列；全文找不到明確問題點時回空陣列（寧缺勿濫）。"
        )
    # 低信心重路由：先前選過但域內找不到貼合細項的域已被排除（負反饋），提示模型換角度重判
    excl = (
        f"注意：先前判入「{'、'.join(excluded_labels)}」但該域內找不到貼合細項（已排除，勿再選）；"
        "請重新思考問題性質改判他域，或確無問題域則回空。\n"
        if excluded_labels
        else ""
    )
    return (
        f"你是 KKday 旅遊商品客訴『歸因域分類器』。{head}\n"
        "先依『問題性質』（頁面描述本身 vs 現場執行/實體/兌換/客服 vs 客人主觀）判屬哪些域，非只看主題字眼。\n"
        "六域界線鐵則（先判屬哪個域）：\n"
        + _domain_boundaries()
        + "\n"
        + excl
        + tail
        + '輸出 JSON：{"domains":["域機器值",...]}'
    )


def _stage_a_domains_multi(
    text: str,
    model: str,
    max_n: int,
    polarity: str = "negative",
    exclude: frozenset[str] = frozenset(),
) -> list[str]:
    """Stage A 多域：判涉及的多個 L1 歸因域（machine value）；負向強制 ≥1 域、混合中性可空；multi 模式停用 self-consistency。

    exclude：低信心重路由的排除域集合（schema enum 硬排除 + prompt 負反饋說明）——
    先前選過但域內湊不出貼合細項的域，重判時不得再選。
    """
    domain_values = [d["code"] for d in ai_judge.selectable_domains() if d["code"] not in exclude]
    if not domain_values:
        return []
    excluded_labels = tuple(ai_judge.domain_label(c) for c in sorted(exclude))
    casc = global_rule.cascade().get("stageA_l1", {})
    sa_model = casc.get("model") or _stage1_model(model)  # 域分類用便宜模型（nano）
    out = _call(
        _stage_a_system_multi(max_n, polarity, excluded_labels),
        _stage_a_user(text),
        "domain",
        sa_model,
        schema=_stage_a_schema_multi(domain_values, max_n),
        effort=_stage_a_effort(),
    )
    seen: set[str] = set()
    doms: list[str] = []
    for d in out.get("domains") or []:
        d = str(d).strip()
        if d in domain_values and d not in seen:
            seen.add(d)
            doms.append(d)
    # 全無效域（LLM abstain / 回幻覺 code）→ 回空，交由 _resolve_attrs_multi 產生未歸因 pending_data，
    # 對齊單路徑 _stage2_attribute_multi 語義；不 fallback 到 domain_values[0]（捏造 content 歸因）。
    return doms[:max_n]


def _resolve_attrs_multi(
    item: dict, text: str, model: str, max_n: int, polarity: str = "negative"
) -> list[dict]:
    """負向/混合中性評論 → 多條淨化 attr dict：cascade 開走 Stage A 多域→逐域 Stage B；否則單次多歸因。

    去重（同 L1 域保留信心最高，因 action/owner 為域級）+ 過濾全 abstain（無域）+ 依 confidence 降冪 + cap。
    """
    if client.is_stub():  # stub 無法真歸因：回空（負向但無違規線 → pending_data）
        return []
    casc = global_rule.cascade()
    amin = _as_float(global_rule.evidence_policy().get("attr_min_confidence"), 0.0)
    if global_rule.prejudge_depth() == "l2":
        # L2 深度：初判只依評論文字，L3 缺商品/訂單佐證不可靠 → 單呼叫 32 面向目錄判到 L1+L2
        # 即收手（省掉整段 Stage B 選葉）；L3 留待接上外部佐證的深判階段。低信心負反饋在函式內。
        attrs = _attrs_l2_multi(item, text, model, max_n, polarity)
    elif casc.get("enabled", False):
        # Stage A 選擇顆粒度（config cascade.stage_a_level）：'l1'＝六域（預設）；'l1l2'＝直選 L2 面向
        # （32 項含 L2 canon，Stage B 候選縮到該面向葉）。選擇/重路由的排除集顆粒度與之一致。
        l1l2 = str(casc.get("stage_a_level") or "l1") == "l1l2"
        if l1l2:
            picks = _stage_a_l2s_multi(text, model, max_n, polarity)
            attrs = [
                _stage_b(item, text, _l2_domain_map().get(c, ""), model, l2_code=c) for c in picks
            ]
        else:
            picks = _stage_a_domains_multi(text, model, max_n, polarity)
            attrs = [_stage_b(item, text, d, model) for d in picks]
        # 低信心負反饋重路由（一次；config cascade.reroute_on_low_conf）：Stage B 棄權（空回）或
        # 低於 attr 閘門＝「選錯、範圍內無貼合細項」的訊號 → 該些選項列入排除集重跑 Stage A
        # （schema 硬排除+prompt 負反饋），給評論改判到正確分類的機會（僅丟棄會漏掉真問題）；
        # 已成立選項一併排除防重複。重判結果同過閘門，確無他處可歸則回空（不硬湊）。
        if casc.get("reroute_on_low_conf", False):
            bad = [
                not a.get("l1_domain_code") or (amin and a.get("confidence", 0.0) < amin)
                for a in attrs
            ]
            rejected = {p for p, b in zip(picks, bad, strict=True) if b}
            if rejected:
                kept = {p for p, b in zip(picks, bad, strict=True) if not b}
                exclude = frozenset(rejected | kept)
                if l1l2:
                    retry = _stage_a_l2s_multi(text, model, max_n, polarity, exclude=exclude)
                    attrs += [
                        _stage_b(item, text, _l2_domain_map().get(c, ""), model, l2_code=c)
                        for c in retry
                        if c not in exclude
                    ]
                else:
                    retry = _stage_a_domains_multi(text, model, max_n, polarity, exclude=exclude)
                    attrs += [_stage_b(item, text, d, model) for d in retry if d not in exclude]
    else:
        attrs = _stage2_attribute_multi(item, text, model, max_n, polarity)
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
    smin = _as_float(global_rule.evidence_policy().get("secondary_min_confidence"), 0.0)
    if smin and len(ranked) > 1:
        ranked = [ranked[0]] + [a for a in ranked[1:] if a.get("confidence", 0.0) >= smin]
    return ranked[:max_n]


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

    全 5 來源統一入口，取代單歸因 to_finding。每條歸因＝一個 TicketFinding（獨立 finding_id、
    L1-L3、信心、分層、判決階段、action），落庫為 judgments 獨立列（見 db.replace_source_findings）。
    進歸因的傾向由 global_rule.polarity_gate.attribute_when 決定（預設 negative+neutral——
    混合中性評論的具體問題點也要歸因，kiki 2026-07-06 反饋）：
    - 正向/純好評/不在 gate 清單 → [單一 non_issue finding]（不歸因）。
    - 負向/混合中性且有歸因 → 每域一條 finding（信心最高標 is_primary；列 polarity＝整則傾向）。
    - 負向但全無法歸類 → [單一負向未歸因 finding]（pending_data）。
    - 混合中性但找不到具體問題點 → [單一 non_issue finding]（judged，非 pending_data——整體無礙）。

    finding_id：非負向/未歸因＝`fd_{item_id}`；多歸因每條＝`fd_{item_id}__{l1_domain}`（域級去重→唯一）。

    Args:
        item: 進線列 dict（intake_items / product_reviews 欄；已 _normalize_raw）。
        model: 主判決模型；stub 走啟發式極性、負向回未歸因單筆。
        voter_cfgs: 跨廠 ensemble voter 的 effective LLM config 清單（None＝不 ensemble，完全維持單模型行為）。
            提供時：主判決有低信心 attr（< auto_accept）才對各 voter 複判 + 投票合併（見 _ensemble_attrs）。
        ensemble_sample_rate: 高信心筆的抽樣稽核比例（④抽樣；0＝純 confidence-gate·>0＝高信心也按比例跑 ensemble）。

    Returns:
        TicketFinding 清單（≥1 筆）；ensemble 觸發時 model_used="ensemble"、model_votes 帶各 voter 票。
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
    if polarity not in _attribute_when():  # config 驅動（global_rule.polarity_gate）
        return _route([_non_issue_finding(item, polarity, used_model, sentiment=sentiment)])

    attrs = _resolve_attrs_multi(item, text, model, _max_attributions(), polarity)
    # confidence-gated ensemble：voter_cfgs 提供且主判決有低信心 attr 時才跨廠複判（高信心直接採信·省 token）
    model_votes: list[dict] = []
    if voter_cfgs and attrs:
        attrs, model_votes = _ensemble_attrs(
            item, text, attrs, model, voter_cfgs, ensemble_sample_rate, polarity
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
    """哪些整體傾向進歸因（global_rule.polarity_gate.attribute_when；SSOT＝DB active 版）。

    容錯：字串（legacy attribute_only_when 單值）與清單皆收；只認 negative/neutral（positive
    恆 non_issue，防 config 誤填放行好評歸因）；config 缺失/全無效 → 回退 {"negative"}（保守舊行為）。
    """
    gate = global_rule.polarity_gate()
    raw = gate.get("attribute_when") or gate.get("attribute_only_when") or []
    vals = [raw] if isinstance(raw, str) else list(raw or [])
    allowed = frozenset(
        v for v in (str(x).strip().lower() for x in vals) if v in ("negative", "neutral")
    )
    return allowed or frozenset({"negative"})


def _l2_domain_map() -> dict[str, str]:
    """L2 C-code → 所屬 L1 域機器值（自攤平葉節點推導；含 L2 葉自身）。stage_a_level=l1l2 用。"""
    return {
        n["l2_code"]: n["l1_domain"] for n in ai_judge.l3_nodes_for_domains([]) if n.get("l2_code")
    }


def _l2_catalog() -> str:
    """全 32 個 L2 面向目錄（code | L1›L2 | L2 canon）；stage_a_level=l1l2 的 Stage A 注入。

    L2 canon 取 ai_judge.l2_judgment（分支判準；L2 葉為其葉判準 canon），缺值留空仍列（code 可選）。
    靜態前綴，隨 DB active 規則版本穩定 → 命中 prompt caching。
    """
    lines: list[str] = []
    seen: set[str] = set()
    for n in ai_judge.l3_nodes_for_domains([]):
        l2 = n.get("l2_code", "")
        if not l2 or l2 in seen:
            continue
        seen.add(l2)
        canon = ai_judge.l2_judgment(l2).get("canon") or ""
        if not canon and n.get("level") == 2:  # L2 葉不在分支判準表 → 用葉自身 canon
            canon = n.get("canon") or ""
        lines.append(f"{l2} | {n.get('l1_label', '')}›{n.get('l2_label', '')} | {canon.strip()}")
    return "\n".join(lines)


def _stage_a_l2_system(
    max_n: int, polarity: str = "negative", excluded_labels: tuple[str, ...] = ()
) -> str:
    """Stage A（l1l2 模式）system：六域界線鐵則 + 32 L2 面向目錄 + 多選輸出格式。

    與 _stage_a_system_multi（L1 模式）同構：先按問題性質判域、再於域內選最貼切面向；
    排除/收尾語義一致（負向強制 ≥1、混合中性/重路由可回空）。
    """
    if polarity == "negative" and not excluded_labels:
        head = "這是負向評論，可能同時涉及多個問題面向。"
        tail = f"列出所有『明確涉及』的 L2 面向 code（最多 {max_n} 個、各不重複、勿湊數）；負向至少一個、不得空。"
    elif polarity == "negative":
        head = "這是負向評論，可能同時涉及多個問題面向。"
        tail = f"列出『明確涉及』的 L2 面向 code（最多 {max_n} 個、各不重複、勿湊數）；確無合適面向則回空陣列。"
    else:
        head = "這是混合傾向評論（整體滿意但提到具體問題點），只針對其中的『問題面向』分類。"
        tail = (
            f"只列出『有具體問題證據』的 L2 面向 code（最多 {max_n} 個、各不重複）；"
            "被稱讚/滿意的面向不列；全文找不到明確問題點時回空陣列（寧缺勿濫）。"
        )
    excl = (
        f"注意：先前判入「{'、'.join(excluded_labels)}」但該面向內找不到貼合細項（已排除，勿再選）；"
        "請重新思考問題性質改判其他面向，或確無問題則回空。\n"
        if excluded_labels
        else ""
    )
    return (
        f"你是 KKday 旅遊商品客訴『歸因分類器』。{head}\n"
        "先依『問題性質』（頁面描述本身 vs 現場執行/實體/兌換/客服 vs 客人主觀）判屬哪個域，"
        "再於該域內選最貼切的 L2 面向，非只看主題字眼。\n"
        "六域界線鐵則（先判屬哪個域）：\n" + _domain_boundaries() + "\n"
        "L2 面向目錄（只能從中選 code）：\n"
        + _l2_catalog()
        + "\n"
        + excl
        + tail
        + '輸出 JSON：{"domains":["L2 面向 code",...]}'
    )


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


def _stage_a_l2s_multi(
    text: str,
    model: str,
    max_n: int,
    polarity: str = "negative",
    exclude: frozenset[str] = frozenset(),
) -> list[str]:
    """Stage A（l1l2 模式）：直選涉及的 L2 面向 code（≤max_n）；exclude＝重路由排除集（schema 硬排除）。"""
    values = sorted(c for c in _l2_domain_map() if c not in exclude)
    if not values:
        return []
    excluded_labels = tuple(ai_judge.path_label(c) or c for c in sorted(exclude))
    casc = global_rule.cascade().get("stageA_l1", {})
    sa_model = casc.get("model") or _stage1_model(model)  # 同 L1 模式：域/面向分類用 Stage A 模型
    out = _call(
        _stage_a_l2_system(max_n, polarity, excluded_labels),
        _stage_a_user(text),
        "domain",
        sa_model,
        schema=_stage_a_schema_multi(values, max_n),
        effort=_stage_a_effort(),
    )
    seen: set[str] = set()
    picks: list[str] = []
    for c in out.get("domains") or []:
        c = str(c).strip()
        if c in values and c not in seen:
            seen.add(c)
            picks.append(c)
    return picks[:max_n]


# ── L2 深度（global_rule.prejudge_depth="l2"）：單呼叫 32 面向目錄多歸因 ────────────
# 初判只依評論文字，L3 細項常缺商品/訂單佐證而不可靠 → 判到 L1+L2 即收手（整段 Stage B 選葉
# 延後），L3 留待接上外部佐證的深判階段。省 token 主力：每條歸因少一次 4-6k prompt 的 Stage B 呼叫。
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


def _attr_schema_l2_multi(codes: frozenset[str], max_n: int) -> dict:
    """L2 多歸因輸出 schema：attributions 陣列（≤max_n），每條一個面向 code + 信心 + 摘要 + 佐證。

    summary 沿用 Stage B 語系陣列格式（表格「摘要」欄消費）；enum 白名單使生成階段即不吐非法 code。
    """
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
                    "required": ["l2_code", "confidence", "summary", "evidence_quote"],
                    "properties": {
                        "l2_code": {"type": "string", "enum": sorted(codes)},
                        "confidence": {"type": "number"},
                        "summary": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["lang", "text"],
                                "properties": {
                                    "lang": {"type": "string"},
                                    "text": {"type": "string"},
                                },
                            },
                        },
                        "evidence_quote": {"type": "string"},
                    },
                },
            }
        },
    }


def _attr_system_l2_multi(
    max_n: int, polarity: str = "negative", excluded_labels: tuple[str, ...] = ()
) -> str:
    """L2 多歸因 system prompt：判官指引 + 六域界線 + 32 面向目錄 + 多條輸出格式（靜態前綴命中 caching）。

    與 _attr_system_multi（L3 版）同構；差異＝目錄只到 L2（初判不選 L3——細項缺商品/訂單佐證，
    留待深判）。excluded_labels＝低信心負反饋重問時的排除說明（schema enum 已硬排除）。
    """
    if polarity == "negative":
        lead = f"這則負向評論可能同時違反多個問題面向。列出所有『明確且互相獨立』的問題歸因（最多 {max_n} 條，"
    else:
        lead = (
            "這是混合傾向評論（整體滿意但提到具體問題點）：只歸因其中的『具體問題點』，"
            f"被稱讚/滿意的面向不歸因。列出問題歸因（最多 {max_n} 條，"
        )
    excl = (
        f"注意：先前判入「{'、'.join(excluded_labels)}」但信心過低（已排除，勿再選）；"
        "請重新思考問題性質改判其他面向，或確無合適面向則回空陣列。\n"
        if excluded_labels
        else ""
    )
    return (
        f"{_attr_sys()}\n\n"
        f"六域界線鐵則（先判屬哪個域，再選面向）：\n{_domain_boundaries()}\n\n"
        f"問題面向目錄（只判到面向層，只能從中選 code）：\n{_l2_catalog()}\n\n"
        + excl
        + lead
        + "每條一個面向 code）：不同問題各歸一條、同一問題勿拆多條、勿為湊數硬加、寧缺勿濫；無法歸類回空陣列。\n"
        '輸出 JSON：{"attributions":[{"l2_code":"面向 code","confidence":0~1 浮點,'
        '"summary":[{"lang":"語言碼","text":"該語言的簡明摘要"},...]（1~3 條·去重·務必含一條 lang="zh-tw"'
        "台灣繁體中文書面語，一句話簡明扼要；原文非繁中另附一條原文語言碼摘要）,"
        '"evidence_quote":"進線中最能佐證的原文片段（保留原文語言，逐字不改寫）"},...]}'
    )


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
    ev = global_rule.evidence_policy()
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


def _attrs_l2_multi(
    item: dict, text: str, model: str, max_n: int, polarity: str = "negative"
) -> list[dict]:
    """L2 深度主流程：單呼叫 32 面向目錄多歸因 → 淨化；低信心負反饋重問一次（同 cascade 開關）。

    低信心反饋（cascade.reroute_on_low_conf 同一開關·語義對齊 cascade 重路由）：首輪有面向
    低於 attr_min_confidence（＝選錯訊號）→ 排除「低信心+已成立」面向後重問一次
    （schema enum 硬排除 + prompt 負反饋），給評論改判到正確面向的機會；重問結果同過
    _resolve_attrs_multi 共用閘門，確無合適面向則不硬湊。
    """
    valid = _l2_label_map()
    codes = frozenset(valid)
    if not codes:
        return []
    out = _call(
        _attr_system_l2_multi(max_n, polarity),
        _attr_user(text),
        "attribute",
        model,
        schema=_attr_schema_l2_multi(codes, max_n),
        effort=_attr_effort(),
    )
    attrs = [
        _finalize_attr_l2(item, text, a, valid)
        for a in (out.get("attributions") or [])[:max_n]
        if isinstance(a, dict)
    ]
    if global_rule.cascade().get("reroute_on_low_conf", False):
        amin = _as_float(global_rule.evidence_policy().get("attr_min_confidence"), 0.0)
        rejected = {
            a["l2_code"]
            for a in attrs
            if a.get("l2_code") and amin and a.get("confidence", 0.0) < amin
        }
        if rejected:
            kept = {
                a["l2_code"] for a in attrs if a.get("l2_code") and a["l2_code"] not in rejected
            }
            exclude = rejected | kept
            retry_codes = frozenset(c for c in codes if c not in exclude)
            if retry_codes:
                excluded_labels = tuple(ai_judge.path_label(c) or c for c in sorted(exclude))
                out2 = _call(
                    _attr_system_l2_multi(max_n, polarity, excluded_labels),
                    _attr_user(text),
                    "attribute",
                    model,
                    schema=_attr_schema_l2_multi(retry_codes, max_n),
                    effort=_attr_effort(),
                )
                attrs += [
                    _finalize_attr_l2(item, text, a, valid)
                    for a in (out2.get("attributions") or [])[:max_n]
                    if isinstance(a, dict) and str(a.get("l2_code", "")) not in exclude
                ]
    return attrs

"""AI 法官核心資料模型（Pydantic v2）。

對應 folder 2117435397 SD §3 的 TicketFinding；前端對應型別見 frontend/src/types/finding.ts。
判決邏輯（classify/adequacy/arbiter/diagnose）將沿用 ProductContentAIChecker 的 Python 資產。
"""

from __future__ import annotations

from typing import Literal, get_args

from pydantic import BaseModel, Field

# 8 大內容治理 dimension label 的單一真相源（legacy 相容欄；roster.DIM_CODE / prejudge 關鍵詞映射皆引用此，
# 不再各自手打中文，杜絕三處副本漂移）。順序即語義順序。
CONTENT_DIMENSIONS: tuple[str, ...] = (
    "商品定位",
    "行程流程",
    "費用資訊",
    "集合資訊",
    "使用兌換",
    "成團條件",
    "限制與風險",
    "承諾與SLA",
)

# Dimension 型別（compile-time Literal 無法由 tuple 展開，故字面重列；下方 assert 保證與 CONTENT_DIMENSIONS 一致）。
Dimension = Literal[
    "商品定位",
    "行程流程",
    "費用資訊",
    "集合資訊",
    "使用兌換",
    "成團條件",
    "限制與風險",
    "承諾與SLA",
    "non_content",  # 出貨/退款/系統/服務等非內容
]

# import 期守衛：Literal 與 tuple 兩份字面若漂移即炸，強制同檔內同步（取代跨檔三副本的隱性 drift）。
assert set(CONTENT_DIMENSIONS) | {"non_content"} == set(get_args(Dimension)), (
    "CONTENT_DIMENSIONS 與 Dimension Literal 不一致，請同步"
)

RecommendedAction = Literal[
    "rewrite_field",
    "fix_contradiction",
    "add_missing_info",
    "clarify_wording",
    "penalize_breach",  # 計點違規 + 要求供應商改善（履約不符）
    "no_action",
    "escalate_ops",
    "escalate_ux",
]


# ── SSOT v2.7 軸A/軸B 共用型別 ──
# 證據層級（漸進升級：純症狀 → 有商品頁 → 有訂單 → 兩者皆有）
# 判決硬閘依此封鎖：< with_order ⇒ 禁判 ②contract_breach
EvidenceLevel = Literal["symptom_only", "with_product_page", "with_order", "with_both"]

# 嚴重度（軸B · ITIL Priority）
Severity = Literal["P0", "P1", "P2", "P3"]

class AdequacyResult(BaseModel):
    """L3 充分度檢查結果（第二意見）。"""

    status: Literal["adequate", "unclear", "missing", "contradictory", "field_empty"]
    evidence: str = ""
    reason: str = ""


class TicketFinding(BaseModel):
    """判決單元（SSOT）。"""

    finding_id: str = ""
    ticket_id: str = ""
    prod_oid: str = ""
    pkg_oid: str = ""
    # L2 問題理解
    dimension: Dimension
    problem_summary: str = ""
    # L3 歸因 + 驗證（LLM 可回 codex 細欄名，故放寬為 str；7 logical field 仍是 adequacy 查詢用）
    suspected_field: str = "none"
    evidence_quote: str = ""  # 害客戶誤解的商品原文段
    ground_truth_quote: str = ""  # 客服對話擷取的正確答案（零幻覺）
    confidence: float = 0.0  # 最終信心（raw → 灰度複判 → cap 封頂 → 線上校準後值）
    raw_confidence: float = 0.0  # arbiter LLM 原始信心（校準輸入；Cleanlab 離線擬合用）
    is_enhanced: bool = False  # 是否經灰度複判（中信賴 [jury_low, jury_high) 重判）
    enhance_model: str = ""  # 複判使用的模型（空＝未複判）
    needs_review: bool = False  # 進人審佇列（校驗兜底 / 低信賴 < jury_low）
    adequacy_check: AdequacyResult | None = None
    # L4 行動
    recommended_action: RecommendedAction
    action_detail: str = ""
    writer_handoff: bool = False  # 防幻覺：content_missing 一律 false
    # 簿記
    is_primary: bool = False
    hit_rule_id: str = (
        ""  # 命中的法典 Rule ID（R1-1~R5-5；codex.scan_misplacement/empty_rule_for 溯源）
    )
    status: Literal["new", "confirmed", "dismissed", "fixed"] = "new"
    created_at: str = ""
    # 感知層來源（管道 A 平台主動 / B 客人進線 / C 供應商申訴）
    source_channel: str = ""  # A_platform | B_customer | C_supplier | unknown
    source_system: str = ""  # 商品評論 / FreshDesk工單 / 訂單訊息 / Feedback / Mixpanel / NPS
    # 執行層對應（誰處理 · 在哪改）
    owner_role: str = ""  # Rule Maker(PM) / Coach(AM·BD) / Referee(QC) / Customer Advocate(CS) / Disciplinary(ERC)
    exec_platform: str = ""  # PM 後台 / SCM2.0·Be2 / 客服系統 / Writer
    order_oid: str = ""  # 訂單編號（B 客人進線可定位具體訂單；A/C 管道通常為空）
    supplier_oid: str = ""  # 供應商編號（order_message 進線可定位；chatbot/平台主動通常為空）
    # ── 軸A intake_vector 轉存（自 NormalizedTicket 複製，dashboard 進線漏斗用）──
    symptom_tag1: str = ""
    symptom_tag2: str = ""
    symptom_tag3: str = ""
    trip_stage: str = ""
    product_category: str = ""
    failure_type: str = ""
    root_cause_candidates: list[str] = Field(default_factory=list)  # 預判候選域（判決前可給）
    evidence_level: EvidenceLevel = "symptom_only"  # 判決當下實際證據層級
    # ── 軸B judgment_vector（判決後 evidence-gated；responsible_party 由 domain 推導）──
    root_cause_domain: str = ""  # 收斂單選歸因域 ①~⑦（候選集不足/卡預判時為空）
    sub_cause: str = ""  # 子類（如：集合執行、語言服務）
    severity: Severity = "P3"  # 嚴重度（本期不判斷，保留預設供既有 pipeline 相容）
    responsible_party: str = ""  # 誰錯（由 root_cause_domain 推導，非 LLM 直接輸出）
    judgment_tier: int | None = None  # v3 判定層（1/2/3A/3B）
    # ── config/ai_judge L3 歸因（prejudge 產出；歸因分類後新增的數據）──
    l1_domain_code: str = ""  # L1 域機器碼（content/supplier…；root_cause_domain 為其圈號）
    l1_label: str = ""  # L1 域中文名
    l2_code: str = ""  # L2 面向 C-code（C-x-y）
    l2_label: str = ""  # L2 面向中文名
    l3_code: str = ""  # L3 細項 C-code（C-x-y-z；config/ai_judge 白名單）
    l3_label: str = ""  # L3 細項中文名
    l3_candidates: list[dict] = Field(default_factory=list)  # top-3 符合度 [{code,label,score}]（透明檢視）
    polarity: str = ""  # 正負傾向：positive(正向) / negative(負向·問題) / neutral / unknown(傾向不明)
    confidence_tier: str = ""  # 信心分層：auto_accept / jury / needs_review
    # 判決階段（prejudge 派生；未判＝無 finding 於 enrich 層補）：
    # judged 已判決 / pending_review 待覆核 / pending_data 待數據補充 / insufficient 資訊不足
    judgment_stage: str = ""
    model_used: str = ""  # 判決使用的 LLM 模型（stub 時為 "stub"）
    judged_at: str = ""  # 判決時間（ISO）

    def to_columns(self) -> dict:
        """判決 payload → judgments typed 欄位 dict（落庫形狀 SSOT）。

        攤平為 typed scalar 欄（可直接 btree 索引 / 乾淨 SQL），只取 14 個真訊號欄；
        殘留 / legacy / 恆空欄（verdict 軸、symptom_tag、severity、evidence_level、
        l3_candidates… 見 plans/1-peaceful-wirth.md）一律不入庫。finding_id / source /
        source_id / prod_oid / dimension / status / created_at / needs_review / true_label
        由 db.findings._finding_values 補齊（來源關聯 + 人工覆核軸）。

        Returns:
            判決 payload 欄位 dict（polarity/stage/l1_code…/conf_value/summary/action…）。
        """
        return {
            "polarity": self.polarity,
            "stage": self.judgment_stage,
            "l1_code": self.l1_domain_code,
            "l1_label": self.l1_label,
            "l2_code": self.l2_code,
            "l2_label": self.l2_label,
            "l3_code": self.l3_code,
            "l3_label": self.l3_label,
            "conf_value": self.confidence,
            "conf_raw": self.raw_confidence,
            "conf_tier": self.confidence_tier,
            "summary": self.problem_summary,
            "evidence": self.evidence_quote,
            "action": self.recommended_action,
            "model": self.model_used,
            "is_primary": self.is_primary,
            "judged_at": self.judged_at,
        }



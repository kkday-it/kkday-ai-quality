"""AI 法官核心資料模型（Pydantic v2）。

對應 folder 2117435397 SD §3 的 TicketFinding；前端對應型別見 frontend/src/types/finding.ts。
判決邏輯（classify/adequacy/arbiter/diagnose）將沿用 ProductContentAIChecker 的 Python 資產。
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

# 8 大內容治理 dimension（取自 ① 審品 rules.json，三支柱共用同一分類法）
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

# 商品可歸因的邏輯欄位（L0 已正規化別名）
LogicalField = Literal[
    "prod_name",
    "prod_summary",
    "prod_feature",
    "prod_schedules",
    "pkg_desc",
    "pkg_schedules",
    "none",
]

# verdict 五分類 —「是不是真內容問題」的判準（L3 靈魂）
Verdict = Literal[
    "real_config_issue",  # 設定寫錯/矛盾 → 進 PM 清單
    "content_missing",    # 該講沒講 → 進 PM 清單
    "content_unclear",    # 模糊易誤解 → 進 PM 清單
    "customer_misread",   # 其實寫清楚了 → 不進清單（UX 洞察）
    "escalate_ops",       # 服務/出貨等非內容 → 不進清單
]

# 進 PM 修改清單的 verdict（純內容問題）
ACTIONABLE_VERDICTS: tuple[Verdict, ...] = (
    "real_config_issue",
    "content_missing",
    "content_unclear",
)

RecommendedAction = Literal[
    "rewrite_field",
    "fix_contradiction",
    "add_missing_info",
    "clarify_wording",
    "no_action",
    "escalate_ops",
    "escalate_ux",
]


class CSTurn(BaseModel):
    role: str = ""  # customer | agent
    content: str = ""


class NormalizedTicket(BaseModel):
    """L1 正規化工單（評論/工單/訂單訊息共用）。"""

    ticket_id: str  # 冪等鍵（review id / thread ts）
    source: Literal["review", "ticket", "order_message", "manual"]
    prod_oid: str = ""
    pkg_oid: str = ""
    lang: str = "zh-tw"
    rating: Optional[int] = None  # 評論星等（嚴重度訊號）
    comment: str = ""  # 客訴/差評自由文字 → L2 主輸入
    cs_conversation: list[CSTurn] = Field(default_factory=list)  # ground truth
    created_at: str = ""


class ProductConfig(BaseModel):
    """L0 商品設定原文。"""

    prod_oid: str
    pkg_oid: str = ""
    fields: dict[str, str] = Field(default_factory=dict)  # 邏輯欄位 → 原文


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
    # L3 歸因 + 驗證
    suspected_field: LogicalField = "none"
    evidence_quote: str = ""       # 害客戶誤解的商品原文段
    ground_truth_quote: str = ""   # 客服對話擷取的正確答案（零幻覺）
    verdict: Verdict
    confidence: float = 0.0
    adequacy_check: Optional[AdequacyResult] = None
    # L4 行動
    recommended_action: RecommendedAction
    action_detail: str = ""
    writer_handoff: bool = False  # 防幻覺：content_missing 一律 false
    # 簿記
    is_primary: bool = False
    status: Literal["new", "confirmed", "dismissed", "fixed", "data_missing"] = "new"
    created_at: str = ""


class InboundItem(BaseModel):
    """待判決標的（人工錄入：CSV/Excel 批量 或 單個新增）。存本地 SQLite，供 L2–L4 判決。"""

    item_id: str = ""  # 冪等鍵（source + prod_oid + comment hash）
    source: Literal["review", "ticket", "manual", "csv", "excel"] = "manual"
    prod_oid: str = ""
    pkg_oid: str = ""
    rating: Optional[int] = None  # 評分/星等（嚴重度訊號）
    comment: str = ""  # 客訴/差評文字（判決主輸入）
    raw: dict = Field(default_factory=dict)  # 原始列（audit）
    status: Literal["pending", "diagnosed", "failed"] = "pending"
    created_at: str = ""

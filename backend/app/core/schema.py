"""AI 法官核心資料模型（Pydantic v2）。

對應 folder 2117435397 SD §3 的 TicketFinding；前端對應型別見 frontend/src/types/finding.ts。
初判邏輯（classify / arbiter）沿用 ProductContentAIChecker 的 Python 資產。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

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


# ── 傾向 ↔ 情緒分的對應區間（**唯一真相源**）──────────────────────────────────
# 兩個方向都從這裡取，避免各寫一份而漂移：
# - 正向：`prejudge._clamp_sentiment` 把 LLM 的情緒分夾進 polarity 對應區間
# - 反向：`db.corrections.polarity_for_sentiment` 由人工填的情緒分**派生**傾向
#   （人工糾正只填情緒分，傾向不讓人另外選——否則會出現「正向＋情緒分 1」這種矛盾組合）
SENTIMENT_BANDS: dict[str, tuple[int, int]] = {
    "negative": (1, 2),
    "neutral": (3, 3),  # 中立恆為 3（doc 定義為單點，不是區間）
    "positive": (4, 5),
}


def polarity_for_sentiment(score: int) -> str:
    """情緒分 → 傾向（1-2 負向 / 3 中立 / 4-5 正向）；超出 1-5 一律回中立。"""
    for polarity, (lo, hi) in SENTIMENT_BANDS.items():
        if lo <= score <= hi:
            return polarity
    return "neutral"


# ── SSOT v2.7 軸A/軸B 共用型別 ──
# 證據層級（漸進升級：純症狀 → 有商品頁 → 有訂單 → 兩者皆有）
# 初判硬閘依此封鎖：< with_order ⇒ 禁判 ②contract_breach

# 嚴重度（軸B · ITIL Priority）


class TicketFinding(BaseModel):
    """初判單元（SSOT）。"""

    ticket_id: str = ""
    pkg_oid: str = ""
    # 反饋摘要：語系 → 簡明摘要 map（LLM 產·去重·務必含 'zh-tw' 台灣繁體；表格只顯示 zh-tw）。空則回退 evidence 片段。
    summary: dict[str, str] = Field(default_factory=dict)
    evidence_quote: str = ""  # 逐字原文佐證（防捏造 grounding 錨點 + FindingCard 佐證欄；非摘要）
    confidence: float = 0.0  # 最終信心（raw → 灰度複判 → cap 封頂 → 線上校準後值）
    raw_confidence: float = 0.0  # arbiter LLM 原始信心（校準輸入；Cleanlab 離線擬合用）
    # L4 行動
    recommended_action: RecommendedAction
    # 簿記
    is_primary: bool = False
    # G1 自動確認路由的結果：高信心且已判定 → true（系統自動採納，不進人工佇列）。
    is_auto_accepted: bool = False
    created_at: str = ""  # ISO 8601；落庫時空字串會被 _finding_values 轉為 None
    # 負責單位（owner_role）不存於 finding：改由 db._shared.attribution_dto 讀取時自 l1_code 派生
    # （ai_judge.domain_owner，SSOT＝rule _meta.owner_role），避免每列 denormalize 一份衍生值。
    order_oid: str = ""  # 訂單編號（B 客人進線可定位具體訂單；A/C 管道通常為空）
    supplier_oid: str = ""  # 供應商編號（order_message 進線可定位；chatbot/平台主動通常為空）
    # ── config/ai_judge L2 歸因（prejudge 產出；歸因分類後新增的數據）──
    l1_domain_code: str = ""  # L1 域機器碼（content/supplier…；root_cause_domain 為其圈號）
    l1_label: str = ""  # L1 域中文名
    l2_code: str = ""  # L2 面向 C-code（C-x-y）
    l2_label: str = ""  # L2 面向中文名
    polarity: str = ""  # 正負傾向：positive(正向) / negative(負向·問題) / neutral(中立)
    # 情緒分 1-5（LLM 讀原文細分，夾進 polarity 區間：負面 1-2 / 中立 3 / 正面 4-5）；0＝未初判。
    # 與外部評論 sentiment 同尺度，供評論對比表逐則比對。
    sentiment_score: int = 0
    confidence_tier: str = ""  # 信心分層：auto_accept / jury / needs_review
    # 初判階段（prejudge 派生；未初判＝無 finding 於 enrich 層補）：
    # judged 已初判 / pending_review 待複審 / pending_data 待數據補充
    prejudge_stage: str = ""
    model_used: str = ""  # 初判使用的 LLM 模型（stub 時為 "stub"）

    def to_columns(self) -> dict:
        """初判 payload → attributions typed 欄位 dict（落庫形狀 SSOT）。

        攤平為 typed scalar 欄（可直接 btree 索引 / 乾淨 SQL），只取真訊號欄；
        非落庫欄（來源關聯、UI 衍生欄）不在此。source /
        source_id / created_at / is_auto_accepted
        由 db.findings._finding_values 補齊（來源關聯 + 人工判決軸）。

        Returns:
            初判 payload 欄位 dict（polarity/stage/l1_code…/conf_value/summary/action…）。
        """
        return {
            "polarity": self.polarity,
            "sentiment_score": self.sentiment_score or None,  # 0/未初判 → NULL
            "prejudge_stage": self.prejudge_stage,
            "l1_code": self.l1_domain_code,
            "l1_label": self.l1_label,
            "l2_code": self.l2_code,
            "l2_label": self.l2_label,
            "conf_value": self.confidence,
            "conf_raw": self.raw_confidence,
            "conf_tier": self.confidence_tier,
            "summary": self.summary,
            "evidence": self.evidence_quote,
            "action": self.recommended_action,
            "model": self.model_used,
            "is_primary": self.is_primary,
        }

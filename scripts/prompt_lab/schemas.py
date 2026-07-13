"""Prompt Lab 資料契約（Pydantic v2）——四類 JSONL 記錄的單一真相源。

對應 PRD §6：CandidateCase / AuditResult / FrozenCase / JudgeRunResult。
所有模型 `extra="forbid"`（PRD §16 拒絕額外欄位），列舉用 Literal 於 parse 時即卡死非法值。

本模組為**離線 Prompt Lab 專用**，不 import backend.app，不觸碰生產判決鏈路（PRD §3）。

L2 code 契約說明（SSOT）：
    C1_L2_CODES 是「C-1 評測合約」的唯一常數（依 PRD §4.2）。被測 judge 能輸出的 code 由
    prompt 的 `## Schema` enum 決定（prompt_parser.extract_schema_enum 動態取出）——兩者理應一致，
    以 test_schemas 對 baseline C-1 prompt 斷言吻合，避免寫死第二份而漂移。
"""

from __future__ import annotations

import re
import unicodedata
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ── C-1 評測合約常數（PRD §4.2；SSOT，見 module docstring）────────────────────
C1_L2_CODES: tuple[str, ...] = (
    "C-1-1",  # 商品定位
    "C-1-2",  # 行程流程
    "C-1-3",  # 費用資訊
    "C-1-4",  # 集合資訊
    "C-1-5",  # 使用／兌換
    "C-1-6",  # 限制與風險
    "C-1-7",  # 退改與服務承諾
)
C1_L2_SET = frozenset(C1_L2_CODES)

# 他域 L2 code 泛型格式（負例的 boundary_with / 真實責任標註用，如 C-3-3、C-6-6）
_L2_CODE_RE = re.compile(r"^C-[1-6]-[0-9]+$")
_DOMAIN_RE = re.compile(r"^C-[1-6]$")  # 純域碼（如 C-2），對照組 boundary_with 可用
# 對照組特殊 boundary token：side B 因「頁面已寫清、根本無問題」而 C-1=false（PRD §5.3「无明确问题」）
_BOUNDARY_TOKENS = frozenset({"no_issue"})


def _valid_boundary(v: str) -> bool:
    """boundary_with 合法性：他域面向碼（C-3-3）、純域碼（C-2）或特殊 token（no_issue）。"""
    return bool(_L2_CODE_RE.match(v) or _DOMAIN_RE.match(v) or v in _BOUNDARY_TOKENS)


DomainLabel = Literal["true", "false", "uncertain"]
Origin = Literal["ai_generated", "human_edited", "human_authored"]
CandidateStatus = Literal[
    "candidate", "audited", "review_required", "accepted", "rejected"
]
AuditStatus = Literal["accepted", "review_required", "rejected"]
Polarity = Literal[
    "negative", "neutral", "positive"
]  # positive 僅少量防禦性樣本（PRD §4.6）


# ── 純函式工具（evidence 落地／去重正規化；metrics 與 validator 共用）──────────
def verbatim_grounded(quote: str, text: str) -> bool:
    """quote 是否為 text 的逐字子串（不正規化、不改寫；PRD §11.3 evidence grounding 判準）。

    空 quote 視為未落地（False）——避免空字串永遠「是子串」的假陽性。
    """
    return bool(quote) and quote in text


def normalize_for_dedup(text: str) -> str:
    """去重正規化：Unicode NFKC + 合併連續空白 + strip（PRD §7 exact-hash 去重前處理）。"""
    nfkc = unicodedata.normalize("NFKC", text)
    return re.sub(r"\s+", " ", nfkc).strip()


def _validate_l2_codes(codes: list[str], *, c1_only: bool, field: str) -> list[str]:
    """校驗 L2 code 清單格式；c1_only=True 時限本域目錄，否則允許他域泛型格式。"""
    for c in codes:
        if c1_only:
            if c not in C1_L2_SET:
                raise ValueError(f"{field}: '{c}' 不在 C-1 目錄 {C1_L2_CODES}")
        elif not _L2_CODE_RE.match(c):
            raise ValueError(f"{field}: '{c}' 非合法 L2 code 格式（預期如 C-3-3）")
    return codes


class CandidateCase(BaseModel):
    """Generator 產出的候選樣本（PRD §6.1）——冻結前的原始標註 + 生成溯源。"""

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(..., min_length=1)  # 全局唯一且穩定
    domain_under_test: str  # 本期恆為 "C-1"
    layer: Literal[1, 2]
    text: str = Field(..., min_length=1)
    input_polarity: Polarity
    expected_domain: DomainLabel
    expected_l2_codes: list[str] = Field(default_factory=list)
    forbidden_l2_codes: list[str] = Field(default_factory=list)
    expected_evidence_quotes: list[str] = Field(default_factory=list)
    case_family: str  # rule_unit | contrast_pair | mixed | uncertain | adversarial | defensive_positive
    expression_variant: str  # direct | colloquial | euphemistic | rhetorical_question | noisy | neutral_mixed | ...
    difficulty: Literal["easy", "medium", "hard"]
    language: str  # zh-tw | zh-cn | en | ja | mixed | ...
    boundary_with: str | None = None  # 近鄰他域 code（如 C-3-3）；正例可為 None
    contrast_pair_id: str | None = None
    contrast_key: str | None = None
    label_reason: str = ""
    generator_model: str = ""
    generator_request_id: str = ""
    generation_plan_id: str = ""
    origin: Origin = "ai_generated"
    status: CandidateStatus = "candidate"

    @field_validator("domain_under_test")
    @classmethod
    def _dut(cls, v: str) -> str:
        if v != "C-1":
            raise ValueError("本期 domain_under_test 僅支援 'C-1'")
        return v

    @field_validator("boundary_with")
    @classmethod
    def _boundary(cls, v: str | None) -> str | None:
        if v is not None and not _valid_boundary(v):
            raise ValueError(f"boundary_with '{v}' 非合法域/面向 code")
        return v

    @model_validator(mode="after")
    def _cross_field(self) -> CandidateCase:
        """跨欄位不變式（PRD §6.1 約束）：L2 數量／證據落地／對照 pair 完整性。"""
        # true → 1~2 個本域 L2；false / uncertain → 必須為空
        if self.expected_domain == "true":
            n = len(self.expected_l2_codes)
            if not (1 <= n <= 2):
                raise ValueError(
                    f"expected_domain=true 需 1~2 個 expected_l2_codes，實得 {n}"
                )
            _validate_l2_codes(
                self.expected_l2_codes, c1_only=True, field="expected_l2_codes"
            )
        elif self.expected_l2_codes:
            raise ValueError(
                f"expected_domain={self.expected_domain} 時 expected_l2_codes 必須為空"
            )
        _validate_l2_codes(
            self.forbidden_l2_codes, c1_only=True, field="forbidden_l2_codes"
        )
        # evidence 必須逐字存在於 text（空清單允許：false/uncertain 常無本域證據）
        for q in self.expected_evidence_quotes:
            if not verbatim_grounded(q, self.text):
                raise ValueError(f"expected_evidence_quote 非 text 逐字子串：{q!r}")
        # contrast_pair_id 與 contrast_key 同生共滅
        if bool(self.contrast_pair_id) != bool(self.contrast_key):
            raise ValueError("contrast_pair_id 與 contrast_key 必須同時提供或同時為空")
        return self


class AuditResult(BaseModel):
    """Auditor（審題器，非被測 judge）對單條 candidate 的獨立審核結果（PRD §6.2）。"""

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(..., min_length=1)
    label_supported: bool
    ambiguous: bool
    self_contained: bool
    contains_independent_c1_issue: bool
    suggested_domain: DomainLabel
    suggested_l2_codes: list[str] = Field(default_factory=list)
    evidence_quotes_valid: bool
    near_duplicate: bool
    audit_reason: str = ""
    auditor_model: str = ""
    auditor_request_id: str = ""
    status: AuditStatus = "review_required"

    @field_validator("suggested_l2_codes")
    @classmethod
    def _sugg(cls, v: list[str]) -> list[str]:
        return _validate_l2_codes(v, c1_only=True, field="suggested_l2_codes")


class FrozenCase(BaseModel):
    """冻結資料集記錄（PRD §6.3）——只留評測欄位 + 審核元資料 + 版本/切分，不留長推理。

    任何修改都應產生新 dataset_version 與新 SHA-256（由 build_dataset 保證）。
    """

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(..., min_length=1)
    domain_under_test: str
    layer: Literal[1, 2]
    text: str = Field(..., min_length=1)
    input_polarity: Polarity
    expected_domain: DomainLabel
    expected_l2_codes: list[str] = Field(default_factory=list)
    forbidden_l2_codes: list[str] = Field(default_factory=list)
    expected_evidence_quotes: list[str] = Field(default_factory=list)
    case_family: str
    expression_variant: str
    difficulty: Literal["easy", "medium", "hard"]
    language: str
    boundary_with: str | None = None
    contrast_pair_id: str | None = None
    contrast_key: str | None = None
    label_reason: str = ""
    origin: Origin
    # 審核元資料（精簡；不含 Generator/Auditor 長推理）
    label_supported: bool
    evidence_quotes_valid: bool
    # 版本與切分
    dataset_version: str
    split: Literal["dev", "holdout"]
    human_reviewed: bool

    @model_validator(mode="after")
    def _cross_field(self) -> FrozenCase:
        """與 CandidateCase 相同的核心不變式（冻結後仍須自洽）。"""
        if self.expected_domain == "true":
            if not (1 <= len(self.expected_l2_codes) <= 2):
                raise ValueError("expected_domain=true 需 1~2 個 expected_l2_codes")
            _validate_l2_codes(
                self.expected_l2_codes, c1_only=True, field="expected_l2_codes"
            )
        elif self.expected_l2_codes:
            raise ValueError("非 true 時 expected_l2_codes 必須為空")
        for q in self.expected_evidence_quotes:
            if not verbatim_grounded(q, self.text):
                raise ValueError(f"expected_evidence_quote 非 text 逐字子串：{q!r}")
        if bool(self.contrast_pair_id) != bool(self.contrast_key):
            raise ValueError("contrast_pair_id 與 contrast_key 必須同時提供或同時為空")
        return self


class JudgeRunResult(BaseModel):
    """單次 judge 呼叫的原始結果記錄（PRD §6.4）——每個 repeat 各存一列，不做多數投票。

    此為「記錄」模型：raw_output 原樣保存，predicted_* 為解析後衍生；schema_valid/error
    分別記錄 refusal/incomplete/parse 失敗，禁止用空輸出偽裝棄權（PRD §10.4）。
    """

    model_config = ConfigDict(extra="forbid")

    run_id: str
    case_id: str
    repeat_index: int
    prompt_version: str
    prompt_sha256: str
    model: str
    request_id: str | None = None
    raw_output: str | None = None
    predicted_domain_hit: bool | None = None
    predicted_l2_codes: list[str] = Field(default_factory=list)
    predicted_evidence_quotes: list[str] = Field(default_factory=list)
    predicted_confidences: list[float] = Field(default_factory=list)
    schema_valid: bool = False
    evidence_grounded: bool | None = None
    latency_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    attempts: int = 0
    error: str | None = (
        None  # None=成功；否則 "schema_invalid" | "refusal" | "incomplete" | "empty" | "api:<msg>"
    )


# ── 生成計畫（PRD §5.3：邊界矩陣編碼為可讀 plan；Generator 按格生成）──────────────
PlanExpected = Literal[
    "true", "false", "uncertain", "pair"
]  # pair=對照組（同時產 true+false 兩側）


class PlanCell(BaseModel):
    """一個生成格（PRD §7 粒度：一 L2＋一 expected＋一 boundary＋一 variant＋一 difficulty）。

    Generator 每次呼叫消化一格、產 target_count 條（3~5 條/呼叫，>5 則自動分批）。
    contrast_pair 格特殊：一格產「一對」共享 contrast_pair_id 的 true/false 樣本。
    """

    model_config = ConfigDict(extra="forbid")

    cell_id: str = Field(..., min_length=1)
    domain_under_test: str
    layer: Literal[1, 2]
    expected_domain: PlanExpected
    focus_l2: (
        str  # 本格圍繞的 C-1 L2（正例=命中碼；負/對照=責任邊界所繫的 C-1 面向），供切片
    )
    target_l2_codes: list[str] = Field(default_factory=list)  # true 格填 1~2；其餘空
    boundary_with: str | None = None
    expression_variant: str
    difficulty: Literal["easy", "medium", "hard"]
    input_polarity: Polarity
    case_family: str
    target_count: int = Field(..., ge=1)
    coverage_note: str = ""
    contrast_theme: str | None = None  # contrast pair 才有：本對只改變的那一個責任事實
    pair_group: int | None = None
    adversarial_techniques: list[str] = Field(default_factory=list)

    @field_validator("focus_l2")
    @classmethod
    def _focus(cls, v: str) -> str:
        if v not in C1_L2_SET:
            raise ValueError(f"focus_l2 '{v}' 不在 C-1 目錄")
        return v

    @model_validator(mode="after")
    def _check(self) -> PlanCell:
        if self.expected_domain == "true":
            if not (1 <= len(self.target_l2_codes) <= 2):
                raise ValueError(f"{self.cell_id}: true 格需 1~2 target_l2_codes")
            _validate_l2_codes(
                self.target_l2_codes, c1_only=True, field="target_l2_codes"
            )
        elif self.expected_domain != "pair" and self.target_l2_codes:
            raise ValueError(
                f"{self.cell_id}: 非 true/pair 格 target_l2_codes 必須為空"
            )
        if self.expected_domain == "pair":
            if self.pair_group is None or not self.contrast_theme:
                raise ValueError(
                    f"{self.cell_id}: pair 格需 pair_group 與 contrast_theme"
                )
            _validate_l2_codes(
                self.target_l2_codes, c1_only=True, field="target_l2_codes"
            )
        if self.boundary_with is not None and not _valid_boundary(self.boundary_with):
            raise ValueError(
                f"{self.cell_id}: boundary_with '{self.boundary_with}' 非法"
            )
        return self


# ── Generator / Auditor 結構化輸出（strict Structured Outputs；PRD §16）──────────
# LLM 只產「內容 + 自評標籤」；case_id / 溯源 / 權威 expected_* 由 orchestrator 依 plan cell 填。
# strict JSON Schema 為手寫（OpenAI strict 要求各層 additionalProperties=false 且全欄 required），
# 與下方 Pydantic 模型互為鏡像；改一處必改另一處（test_schemas 斷言吻合）。


class GeneratorCaseOut(BaseModel):
    """Generator 單條產出（純內容 + 逐字證據 + 理由；標籤權威值由 plan cell 決定）。"""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(..., min_length=1)
    evidence_quotes: list[str] = Field(default_factory=list)
    label_reason: str = ""
    language: str = "zh-tw"
    pair_side: Literal["A", "B"] | None = (
        None  # 僅 contrast_pair：A=C-1 true 側、B=false 側
    )


class GeneratorOutput(BaseModel):
    """Generator 單次呼叫產出的一批 case（3~5 條）。"""

    model_config = ConfigDict(extra="forbid")

    cases: list[GeneratorCaseOut]


class AuditorOutput(BaseModel):
    """Auditor 單條審核產出（9 個判斷欄；case_id/model/status 由 orchestrator 補）。"""

    model_config = ConfigDict(extra="forbid")

    label_supported: bool
    ambiguous: bool
    self_contained: bool
    contains_independent_c1_issue: bool
    suggested_domain: DomainLabel
    suggested_l2_codes: list[str] = Field(default_factory=list)
    evidence_quotes_valid: bool
    near_duplicate: bool
    audit_reason: str = ""

    @field_validator("suggested_l2_codes")
    @classmethod
    def _sugg(cls, v: list[str]) -> list[str]:
        return _validate_l2_codes(v, c1_only=True, field="suggested_l2_codes")


# 手寫 strict JSON Schema（送 Responses API text.format 用）──────────────────────
GENERATOR_OUTPUT_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["cases"],
    "properties": {
        "cases": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "text",
                    "evidence_quotes",
                    "label_reason",
                    "language",
                    "pair_side",
                ],
                "properties": {
                    "text": {"type": "string"},
                    "evidence_quotes": {"type": "array", "items": {"type": "string"}},
                    "label_reason": {"type": "string"},
                    "language": {"type": "string"},
                    "pair_side": {"type": ["string", "null"], "enum": ["A", "B", None]},
                },
            },
        }
    },
}

AUDITOR_OUTPUT_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "label_supported",
        "ambiguous",
        "self_contained",
        "contains_independent_c1_issue",
        "suggested_domain",
        "suggested_l2_codes",
        "evidence_quotes_valid",
        "near_duplicate",
        "audit_reason",
    ],
    "properties": {
        "label_supported": {"type": "boolean"},
        "ambiguous": {"type": "boolean"},
        "self_contained": {"type": "boolean"},
        "contains_independent_c1_issue": {"type": "boolean"},
        "suggested_domain": {"type": "string", "enum": ["true", "false", "uncertain"]},
        "suggested_l2_codes": {
            "type": "array",
            "items": {"type": "string", "enum": list(C1_L2_CODES)},
        },
        "evidence_quotes_valid": {"type": "boolean"},
        "near_duplicate": {"type": "boolean"},
        "audit_reason": {"type": "string"},
    },
}


class Plan(BaseModel):
    """一層的完整生成計畫；total_target 必須嚴格等於各格 target_count 之和（Phase 0 DoD）。"""

    model_config = ConfigDict(extra="forbid")

    plan_id: str
    domain_under_test: str
    layer: Literal[1, 2]
    description: str = ""
    total_target: int
    cells: list[PlanCell]

    @model_validator(mode="after")
    def _sum(self) -> Plan:
        actual = sum(c.target_count for c in self.cells)
        if actual != self.total_target:
            raise ValueError(
                f"{self.plan_id}: 各格總數 {actual} != total_target {self.total_target}"
            )
        ids = [c.cell_id for c in self.cells]
        if len(ids) != len(set(ids)):
            raise ValueError(f"{self.plan_id}: cell_id 重複")
        return self

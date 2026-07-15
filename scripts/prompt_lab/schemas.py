"""Prompt Lab 資料契約（Pydantic v2）——四類 JSONL 記錄的單一真相源。

對應 PRD §6：CandidateCase / AuditResult / FrozenCase / JudgeRunResult。
所有模型 `extra="forbid"`（PRD §16 拒絕額外欄位），列舉用 Literal 於 parse 時即卡死非法值。

本模組為**離線 Prompt Lab 專用**，不 import backend.app，不觸碰生產判決鏈路（PRD §3）。

L2 code 契約說明（SSOT）：
    DOMAIN_L2_CODES 保存 Prompt Lab 已啟用域的合法 L2；被測 judge 能輸出的 code 仍由各自
    prompt 的 `## Schema` enum 決定。test_schemas 逐域斷言兩者吻合，避免計畫、資料與 prompt 漂移。
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Literal

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
C2_L2_CODES: tuple[str, ...] = (
    "C-2-1",  # 網路品質
    "C-2-2",  # 餐飲品質
    "C-2-3",  # 車輛設備
    "C-2-4",  # 住宿品質
    "C-2-5",  # 設施設備
)
C2_L2_SET = frozenset(C2_L2_CODES)
C3_L2_CODES: tuple[str, ...] = tuple(f"C-3-{i}" for i in range(1, 8))
C4_L2_CODES: tuple[str, ...] = tuple(f"C-4-{i}" for i in range(1, 4))
C5_L2_CODES: tuple[str, ...] = tuple(f"C-5-{i}" for i in range(1, 4))
C6_L2_CODES: tuple[str, ...] = tuple(f"C-6-{i}" for i in range(1, 7))
DOMAIN_L2_CODES: dict[str, tuple[str, ...]] = {
    "C-1": C1_L2_CODES,
    "C-2": C2_L2_CODES,
    "C-3": C3_L2_CODES,
    "C-4": C4_L2_CODES,
    "C-5": C5_L2_CODES,
    "C-6": C6_L2_CODES,
}

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


def l2_codes_for_domain(domain: str) -> tuple[str, ...]:
    """回傳已啟用評測域的合法 L2；未知域 fail-loud。"""
    try:
        return DOMAIN_L2_CODES[domain]
    except KeyError as e:
        raise ValueError(f"Prompt Lab 尚未啟用 domain_under_test={domain!r}") from e


def _validate_l2_codes(
    codes: list[str], *, domain: str | None = None, field: str
) -> list[str]:
    """校驗 L2 code；指定 domain 時限該域目錄，否則只驗通用格式。"""
    allowed = frozenset(l2_codes_for_domain(domain)) if domain else None
    for c in codes:
        if allowed is not None and c not in allowed:
            raise ValueError(
                f"{field}: '{c}' 不在 {domain} 目錄 {l2_codes_for_domain(domain)}"
            )
        if allowed is None and not _L2_CODE_RE.match(c):
            raise ValueError(f"{field}: '{c}' 非合法 L2 code 格式（預期如 C-3-3）")
    return codes


class CandidateCase(BaseModel):
    """Generator 產出的候選樣本（PRD §6.1）——冻結前的原始標註 + 生成溯源。"""

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(..., min_length=1)  # 全局唯一且穩定
    domain_under_test: str
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
        l2_codes_for_domain(v)
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
                self.expected_l2_codes,
                domain=self.domain_under_test,
                field="expected_l2_codes",
            )
        elif self.expected_l2_codes:
            raise ValueError(
                f"expected_domain={self.expected_domain} 時 expected_l2_codes 必須為空"
            )
        _validate_l2_codes(
            self.forbidden_l2_codes,
            domain=self.domain_under_test,
            field="forbidden_l2_codes",
        )
        # true 必须有逐字证据；false/uncertain 必须为空。
        if self.expected_domain == "true" and not self.expected_evidence_quotes:
            raise ValueError("expected_domain=true 需至少 1 条逐字 expected_evidence_quote")
        if self.expected_domain != "true" and self.expected_evidence_quotes:
            raise ValueError(f"expected_domain={self.expected_domain} 时 evidence 必须为空")
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
    domain_under_test: str = "C-1"
    label_supported: bool
    ambiguous: bool
    self_contained: bool
    contains_independent_target_issue: bool
    suggested_domain: DomainLabel
    suggested_l2_codes: list[str] = Field(default_factory=list)
    evidence_quotes_valid: bool
    near_duplicate: bool
    pair_minimality_valid: bool = True
    review_required: bool = False
    audit_reason: str = ""
    auditor_model: str = ""
    auditor_request_id: str = ""
    latency_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    attempts: int = 0
    status: AuditStatus = "review_required"

    @model_validator(mode="before")
    @classmethod
    def _legacy_issue_field(cls, data: Any) -> Any:
        """读取旧 C1/C2 JSONL 时统一映射到 contains_independent_target_issue。"""
        if isinstance(data, dict) and "contains_independent_target_issue" not in data:
            data = dict(data)
            for legacy in ("contains_independent_c1_issue", "contains_independent_c2_issue"):
                if legacy in data:
                    data["contains_independent_target_issue"] = data.pop(legacy)
                    break
        return data

    @model_validator(mode="after")
    def _domain_l2(self) -> AuditResult:
        l2_codes_for_domain(self.domain_under_test)
        _validate_l2_codes(
            self.suggested_l2_codes,
            domain=self.domain_under_test,
            field="suggested_l2_codes",
        )
        return self


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
        l2_codes_for_domain(self.domain_under_test)
        if self.expected_domain == "true":
            if not (1 <= len(self.expected_l2_codes) <= 2):
                raise ValueError("expected_domain=true 需 1~2 個 expected_l2_codes")
            _validate_l2_codes(
                self.expected_l2_codes,
                domain=self.domain_under_test,
                field="expected_l2_codes",
            )
        elif self.expected_l2_codes:
            raise ValueError("非 true 時 expected_l2_codes 必須為空")
        if self.expected_domain == "true" and not self.expected_evidence_quotes:
            raise ValueError("expected_domain=true 需至少 1 条逐字 expected_evidence_quote")
        if self.expected_domain != "true" and self.expected_evidence_quotes:
            raise ValueError(f"expected_domain={self.expected_domain} 时 evidence 必须为空")
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
    focus_l2: str  # 本格圍繞的受測域 L2，供切片
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

    @model_validator(mode="after")
    def _check(self) -> PlanCell:
        _validate_l2_codes(
            [self.focus_l2], domain=self.domain_under_test, field="focus_l2"
        )
        if self.expected_domain == "true":
            if not (1 <= len(self.target_l2_codes) <= 2):
                raise ValueError(f"{self.cell_id}: true 格需 1~2 target_l2_codes")
            _validate_l2_codes(
                self.target_l2_codes,
                domain=self.domain_under_test,
                field="target_l2_codes",
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
                self.target_l2_codes,
                domain=self.domain_under_test,
                field="target_l2_codes",
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
        return _validate_l2_codes(v, domain="C-1", field="suggested_l2_codes")


class C2AuditorOutput(BaseModel):
    """C-2 Auditor 結構化輸出；欄位名維持可讀的 contains_independent_c2_issue。"""

    model_config = ConfigDict(extra="forbid")

    label_supported: bool
    ambiguous: bool
    self_contained: bool
    contains_independent_c2_issue: bool
    suggested_domain: DomainLabel
    suggested_l2_codes: list[str] = Field(default_factory=list)
    evidence_quotes_valid: bool
    near_duplicate: bool
    audit_reason: str = ""

    @field_validator("suggested_l2_codes")
    @classmethod
    def _sugg(cls, v: list[str]) -> list[str]:
        return _validate_l2_codes(v, domain="C-2", field="suggested_l2_codes")


class TargetAuditorOutput(BaseModel):
    """C3～C6 统一 Auditor 输出；域内 enum 由 auditor_contract 动态 schema 约束。"""

    model_config = ConfigDict(extra="forbid")

    label_supported: bool
    ambiguous: bool
    self_contained: bool
    contains_independent_target_issue: bool
    suggested_domain: DomainLabel
    suggested_l2_codes: list[str] = Field(default_factory=list)
    evidence_quotes_valid: bool
    near_duplicate: bool
    pair_minimality_valid: bool
    review_required: bool
    audit_reason: str = ""

    @field_validator("suggested_l2_codes")
    @classmethod
    def _sugg(cls, v: list[str]) -> list[str]:
        return _validate_l2_codes(v, field="suggested_l2_codes")


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

C2_AUDITOR_OUTPUT_SCHEMA: dict = {
    **AUDITOR_OUTPUT_SCHEMA,
    "required": [
        "contains_independent_c2_issue"
        if x == "contains_independent_c1_issue"
        else x
        for x in AUDITOR_OUTPUT_SCHEMA["required"]
    ],
    "properties": {
        **{
            k: v
            for k, v in AUDITOR_OUTPUT_SCHEMA["properties"].items()
            if k not in {"contains_independent_c1_issue", "suggested_l2_codes"}
        },
        "contains_independent_c2_issue": {"type": "boolean"},
        "suggested_l2_codes": {
            "type": "array",
            "items": {"type": "string", "enum": list(C2_L2_CODES)},
        },
    },
}


def _target_auditor_schema(domain: str) -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(TargetAuditorOutput.model_fields),
        "properties": {
            "label_supported": {"type": "boolean"},
            "ambiguous": {"type": "boolean"},
            "self_contained": {"type": "boolean"},
            "contains_independent_target_issue": {"type": "boolean"},
            "suggested_domain": {"type": "string", "enum": ["true", "false", "uncertain"]},
            "suggested_l2_codes": {
                "type": "array",
                "items": {"type": "string", "enum": list(l2_codes_for_domain(domain))},
            },
            "evidence_quotes_valid": {"type": "boolean"},
            "near_duplicate": {"type": "boolean"},
            "pair_minimality_valid": {"type": "boolean"},
            "review_required": {"type": "boolean"},
            "audit_reason": {"type": "string"},
        },
    }


def auditor_contract(domain: str) -> tuple[type[BaseModel], dict, str]:
    """取得各域 Auditor 的 Pydantic 模型、strict schema、独立问题字段名。"""
    if domain == "C-1":
        return AuditorOutput, AUDITOR_OUTPUT_SCHEMA, "contains_independent_c1_issue"
    if domain == "C-2":
        return C2AuditorOutput, C2_AUDITOR_OUTPUT_SCHEMA, "contains_independent_c2_issue"
    if domain in {"C-3", "C-4", "C-5", "C-6"}:
        return TargetAuditorOutput, _target_auditor_schema(domain), "contains_independent_target_issue"
    raise ValueError(f"Prompt Lab 尚未启用 Auditor domain={domain!r}")


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
        l2_codes_for_domain(self.domain_under_test)
        mismatched = [
            c.cell_id for c in self.cells if c.domain_under_test != self.domain_under_test
        ]
        if mismatched:
            raise ValueError(
                f"{self.plan_id}: cell domain 与 plan 不一致：{mismatched[:5]}"
            )
        actual = sum(c.target_count for c in self.cells)
        if actual != self.total_target:
            raise ValueError(
                f"{self.plan_id}: 各格總數 {actual} != total_target {self.total_target}"
            )
        ids = [c.cell_id for c in self.cells]
        if len(ids) != len(set(ids)):
            raise ValueError(f"{self.plan_id}: cell_id 重複")
        return self

"""確定性機器檢查 kernel（從 ProductContentAIChecker writer_judge.run_machine_checks 重整）。

來源：ai_writer_mvp/run/backend/writer_judge.py（1012 行）的零 LLM 規則層。
重整原則：原 run_machine_checks 綁死 writer 三欄位（product_name/highlights/description）；
AI 法官不生成這些欄位、而是「對照客訴判內容欄位是否充分」，故抽出**欄位無關**的確定性
primitives（禁詞 + GT 豁免 / CJK·latin 長度 / 促銷括號 / 結構偵測），讓 adequacy/arbiter
可對任何內容欄位套用。LLM 評分層（RUBRIC / FIELD_WEIGHTS / normalize）不沿用——法官有自己的
arbiter/diagnose。禁詞/情緒詞清單讀同目錄 writer_rules.json（rules.json 逐字搬入）。

只依賴 stdlib（re / json / unicodedata）。零幻覺、零 LLM 成本，適合作判決鏈第一道閘門。
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

_RULES_PATH = Path(__file__).resolve().parent / "writer_rules.json"

# 商品名稱長度依語言判定（rubric §1.2）：CJK/ja/ko 全形=1 半形=0.5，門檻 35；
# en/th/vi 計原始字元（含空白），門檻 60。空/未知語言回退 CJK（KKday 主市場）。
_LATIN_LENGTH_LANGS = {"en", "th", "vi"}

# 促銷括號標籤（【1人成團】【限定】【特價】…）。KKday 商品名中【】幾乎必為促銷/方案標籤、
# 非真實地名 → 命中近乎零誤報。屬方案/行銷層，置前會把高價值關鍵字擠出標題前段。
_PROMO_BRACKET_RE = re.compile(r"【[^】]{0,20}】")

# 結構偵測：active writer prompt 刻意禁 `##` 標題、用無標題編號段（"1. 東京迪士尼樂園"）；
# 多路線則用 `### {分組}路線`（含 ##）。兩者皆算有結構，只有完全無分段的長文才標記。
_NUMBERED_SECTION_RE = re.compile(r"(?m)^\s*\d+[.．、)）]\s*\S")


def load_writer_rules() -> dict:
    """讀 vendored/writer_rules.json（ProductContentAIChecker 撰寫法典逐字搬入）。"""
    return json.loads(_RULES_PATH.read_text(encoding="utf-8"))


def forbidden_terms() -> list[str]:
    """29 禁詞（必去/必玩/最強…誇大行銷詞）。"""
    return [str(x) for x in load_writer_rules().get("forbidden_terms", []) if str(x).strip()]


def emotional_terms() -> list[str]:
    """10 情緒詞（難忘/感動/震撼…主觀情緒詞）。"""
    return [str(x) for x in load_writer_rules().get("emotional_terms", []) if str(x).strip()]


def dimensions() -> dict:
    """8 面向中文定義（與 schema.Dimension 同源）。"""
    return load_writer_rules().get("dimensions", {})


def forbidden_term_hits(text: str, ground_truth: str = "") -> list[str]:
    """命中的禁詞清單（GT 逐字豁免）。

    禁詞除非逐字複製商家原文（ground_truth）否則不得使用——避免對官方專有名詞誤報
    （夢幻樂園/夢幻泉鄉含「夢幻」；保證入場含「保證」）。GT 空＝不豁免（保留 legacy）。
    """
    text = text or ""
    gt = ground_truth or ""
    return [t for t in forbidden_terms() if t and t in text and t not in gt]


def emotional_term_hits(text: str) -> list[str]:
    """命中的情緒詞清單（無 GT 豁免；情緒詞屬主觀渲染，原文有也算）。"""
    text = text or ""
    return [t for t in emotional_terms() if t and t in text]


def measure_name_length(text: str, lang: str = "") -> tuple[float, float]:
    """回 (實測長度, 門檻)，依語言（CJK 全形=1 半形=0.5 門檻35 / latin 原始字元 門檻60）。"""
    base = (lang or "").strip().lower().split("-")[0]
    if base in _LATIN_LENGTH_LANGS:
        return float(len(text or "")), 60.0
    total = 0.0
    for ch in text or "":
        total += 1.0 if unicodedata.east_asian_width(ch) in ("F", "W") else 0.5
    return total, 35.0


def name_length_severity(text: str, lang: str = "") -> str | None:
    """商品名稱長度違規等級：'severe'(>門檻×1.2) / 'over'(>門檻) / None。"""
    if not text:
        return None
    measure, threshold = measure_name_length(text, lang)
    if measure > threshold * 1.2:
        return "severe"
    if measure > threshold:
        return "over"
    return None


def promo_bracket(text: str) -> tuple[str, bool] | None:
    """偵測促銷括號標籤 → (標籤文字, 是否置於開頭)；無則 None。"""
    if not text:
        return None
    m = _PROMO_BRACKET_RE.search(text)
    if not m:
        return None
    return m.group(0), text.lstrip().startswith("【")


def has_section_structure(text: str) -> bool:
    """文本是否具分段結構（含 ## 標題 或 ≥2 個編號段）；否＝整片長文。"""
    text = text or ""
    return "##" in text or len(_NUMBERED_SECTION_RE.findall(text)) >= 2


def check_field(field: str, text: str, lang: str = "", ground_truth: str = "") -> list[dict]:
    """對單一內容欄位跑全部確定性檢查 → findings（欄位無關，可用於任何 dimension/欄位）。

    finding = {rule, severity, evidence, hits?}；severity ∈ critical|high|medium|low。
    供 AI 法官 arbiter 第一道零 LLM 閘門：命中即可直接定調（如禁詞→real_config_issue 傾向）。
    """
    findings: list[dict] = []
    text = (text or "").strip()
    if not text:
        findings.append(
            {"rule": "empty_output", "severity": "critical", "field": field, "evidence": ""}
        )
        return findings

    fb = forbidden_term_hits(text, ground_truth)
    if fb:
        findings.append(
            {
                "rule": "forbidden_terms",
                "severity": "high",
                "field": field,
                "hits": fb[:8],
                "evidence": "、".join(fb[:8]),
            }
        )
    emo = emotional_term_hits(text)
    if emo:
        findings.append(
            {
                "rule": "emotional_terms",
                "severity": "low",
                "field": field,
                "hits": emo[:8],
                "evidence": "、".join(emo[:8]),
            }
        )

    if field in ("prod_name", "product_name"):
        sev = name_length_severity(text, lang)
        if sev:
            measure, threshold = measure_name_length(text, lang)
            findings.append(
                {
                    "rule": f"product_name_length_{sev}",
                    "severity": "medium" if sev == "severe" else "low",
                    "field": field,
                    "evidence": f"長度 {measure:g}/{threshold:g}",
                }
            )
        pb = promo_bracket(text)
        if pb:
            label, leading = pb
            findings.append(
                {
                    "rule": "product_name_promo_bracket",
                    "severity": "medium" if leading else "low",
                    "field": field,
                    "evidence": label + ("（佔據前段）" if leading else ""),
                }
            )

    if field in ("description", "prod_summary", "prod_desc") and not has_section_structure(text):
        findings.append(
            {
                "rule": "description_markdown_structure",
                "severity": "medium",
                "field": field,
                "evidence": text[:120],
            }
        )

    return findings

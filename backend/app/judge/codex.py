"""內容治理法典 — 機器檢查規則庫載入器（judge_rules.json）。

法典完整 SSOT = Google Sheets（58 欄位）；本檔提供 AI 法官 arbiter/diagnose
對 Phase1 30 條可機器檢查規則（R1-1~R5-5）的程式化查詢介面。
對應 specs/05-content-governance-codex.md。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

# 統一判斷邏輯配置（judge_logic_config.json，由 data/parse_judge_logic.py 從 Sheet 3 分頁生成）。
# 為 judge_rules.json 超集：同 30 rule_id + 雙語 en + Phase/扣分加分 + severity + verdict_hint。
_RULES_PATH = Path(__file__).resolve().parent / "judge_logic_config.json"


@lru_cache(maxsize=1)
def _codex() -> dict:
    with _RULES_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def all_rules() -> list[dict]:
    """回傳全部判斷邏輯規則（R1-1~R5-5，含雙語/Phase/severity/verdict_hint）。"""
    return _codex()["rules"]


def get_rule(rule_id: str) -> dict | None:
    """依 Rule ID（如 R1-1）取單條規則，找不到回 None。"""
    return next((r for r in all_rules() if r["rule_id"] == rule_id), None)


def rules_by_dimension(dimension: str) -> list[dict]:
    """取某 dimension 的所有規則（面向正規化比對，容忍『使用／兌換』vs『使用兌換』等標籤差異）。"""
    nd = _norm_dim(dimension)
    return [r for r in all_rules() if _norm_dim(r["dimension"]) == nd]


def verdict_hint_of(rule_id: str) -> str | None:
    """Rule ID → verdict_hint（供 arbiter 套用法典建議判決）。"""
    rule = get_rule(rule_id)
    return rule.get("verdict_hint") if rule else None


def severity_of(rule_id: str) -> str:
    """Rule ID → P1/P2/P3（依 risk_level High/Medium/Low 映射）。"""
    rule = get_rule(rule_id)
    if not rule:
        return "P3"
    return _codex()["risk_to_severity"].get(rule["risk_level"], "P3")


def contract_breach_rules() -> list[dict]:
    """承諾與SLA 類規則：事前查缺失→content_missing，事後已揭露未履約→contract_breach。"""
    return [r for r in all_rules() if r.get("contract_breach_applicable")]


# ── 法典 R 規則的「確定性可機器檢查」子集（零 LLM，供 arbiter 套用）──────────
# verification_logic 多需 LLM；以下為可程式化偵測者：錯位行銷/成團關鍵字（R5-2/R5-3）。
# keyword 取自對應規則 verification_logic（「資訊包含 保證成團 / 買一送一、50% off 等」）。
_MISPLACEMENT_KEYWORDS: dict[str, list[str]] = {
    "R5-2": ["保證成團", "保證出團", "保證成行", "100% 成團", "100%成團"],
    "R5-3": ["買一送一", "50% off", "50%off", "b1g1", "第二件", "限時特價", "下殺", "破盤"],
}


def scan_misplacement(text: str) -> list[dict]:
    """掃描錯位行銷/成團關鍵字（法典 R5-2/R5-3）→ 命中規則清單（含 verdict_hint/flag）。

    僅應對「命名/行銷類欄位」原文掃描（商品名稱/特色/摘要/方案描述）——這些欄位出現
    成團或行銷關鍵字即為錯位（real_config_issue）。注意事項等揭露欄位不適用，勿全欄掃。
    """
    low = (text or "").lower()
    hits: list[dict] = []
    for rid, kws in _MISPLACEMENT_KEYWORDS.items():
        fired = [k for k in kws if k.lower() in low]
        if fired:
            r = get_rule(rid) or {}
            hits.append(
                {
                    "rule_id": rid,
                    "verdict_hint": r.get("verdict_hint", "real_config_issue"),
                    "flag_message": r.get("flag_message", ""),
                    "severity": r.get("severity", "P2"),
                    "keywords": fired,
                }
            )
    return hits


def empty_rule_for(dimension: str) -> str:
    """某面向的『欄位為空』檢查規則 rule_id（供 content_missing 判決溯源；無則空字串）。"""
    nd = _norm_dim(dimension)
    for r in all_rules():
        vl = r.get("verification_logic", "")
        if _norm_dim(r["dimension"]) == nd and ("空" in vl or "empty" in vl.lower()):
            return r["rule_id"]
    return ""


# ── 欄位級法典（field_codex.json，60 欄位）+ judge prompt 生成器 ──────────
_FIELD_PATH = Path(__file__).resolve().parent / "field_codex.json"


@lru_cache(maxsize=1)
def _field_codex() -> dict:
    with _FIELD_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def all_fields() -> list[dict]:
    """回傳全部 60 欄位法典（dimension × field × canon/範例/機器規則）。"""
    return _field_codex()["fields"]


def _norm_dim(s: str) -> str:
    """面向標籤正規化：消弭 field_codex（『使用／兌換』『承諾與 SLA』）與 schema.Dimension
    （『使用兌換』『承諾與SLA』）的斜線/空格差異，避免比對漏接。"""
    return (s or "").replace(" ", "").replace("　", "").replace("／", "").replace("/", "")


def get_field(dimension: str, field: str) -> dict | None:
    """依 面向 + 欄位名 取單欄法典（面向正規化比對；field 支援前綴比對，如『集合地點』）。"""
    nd = _norm_dim(dimension)
    for f in all_fields():
        if _norm_dim(f["dimension"]) == nd and (
            f["field"] == field or f["field"].startswith(field)
        ):
            return f
    return None


def _bullets(xs: list[str]) -> str:
    return "\n".join(f"- {x}" for x in xs) if xs else "（無）"


def build_field_prompt(fd: dict) -> str:
    """欄位法典 → 該欄位的 AI 法官 judge prompt（統一深度模板）。

    模板吸收 ProductContentAIChecker G1/GEN-1 的深度結構（角色→唯一判準→好壞範例→
    輸出 schema→防過擬鐵則）+ L2-L4 SD 的雙意見/防幻覺仲裁原則。
    """
    return f"""# AI 法官 · 欄位判決 Prompt — {fd["dimension"]} / {fd["field"]}

> 自動生成自內容治理法典（field_codex.json）。判準 SSOT = Google Sheets 法典。
> 角色：旅遊電商內容稽核員，**只依本欄位法典判決**，不得以法典未列出的理由扣分（裁判是法典執行器）。
> 只看本欄位原文 + 客服對話(ground truth)，**不採信客訴語氣**。

## 0. 判決目標（治理原則 · Why）
{fd["principle"]}

## 1. 法典條文（Canon · 唯一判準）
{fd["canon"]}

## 2. 允許 ✅ / 禁止 ❌
**✅ 允許**
{_bullets(fd["allow"])}

**❌ 禁止**
{_bullets(fd["deny"])}

## 3. 好範例（應判 Pass）
{_bullets(fd["good_examples"])}

## 4. 壞範例（Red Flag · 應判 Flag）
{_bullets(fd["bad_examples"])}

## 5. 可機器檢查線索
{_bullets(fd["machine_rule"]) if isinstance(fd["machine_rule"], list) else fd["machine_rule"]}

## 6. 判決輸出（嚴格 JSON，response_format=json_object）
{{
  "dimension": "{fd["dimension"]}",
  "field": "{fd["field"]}",
  "violation": true/false,
  "verdict": "real_config_issue | content_missing | content_unclear | contract_breach | customer_misread | escalate_ops",
  "evidence_quote": "命中問題的欄位原文片段；無則空字串",
  "ground_truth_quote": "客服對話中的標準答案/政策原文；無則空字串",
  "reason": "對照 canon 的違規理由（典型違規原因：{fd["violation_reason"]}）",
  "confidence": 0.0,
  "flag_message": "若 violation=true 的一句話標記訊息"
}}

## 7. 判決鐵則（防過擬 · 雙意見 · 防幻覺）
- **欄位已清楚交代** → 即使有客訴也判 `customer_misread`（內容沒錯，屬呈現/UX）。
- **canon 未列出的理由不得扣分**——只用本欄位法典，不自行延伸新規範。
- **缺事實**（缺政策/價格/規則等真實資訊）→ `content_missing`，標記需 PM 補真實資訊，**writer 不可自動生成**（防幻覺）。
- **內容合規但供應商未履約**（如已寫含接送卻沒接送）→ `contract_breach`（計點違規 ERC）。
- **非內容**（出貨/系統/客服態度/服務）→ `escalate_ops`。
- **客服需搬政策原文才能解釋** = 頁面對一般讀者不夠清楚 → 傾向 `content_unclear`/`content_missing`，不可因「細則裡有寫」就判 adequate。
- `confidence` 反映「這是內容問題」的把握，非客訴語氣強度。
"""


def write_all_field_prompts(out_dir: Path) -> int:
    """批次生成 60 欄位 judge prompt 到 out_dir/{dimension}/{field}.md，回傳檔數。"""
    import re

    n = 0
    for fd in all_fields():
        safe_field = re.sub(r"[/\s（）()]+", "_", fd["field"]).strip("_")
        d = out_dir / fd["dimension"]
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{safe_field}.md").write_text(build_field_prompt(fd), encoding="utf-8")
        n += 1
    return n


# ── adequacy 判準解析（沿用深度 prompt 優先 → field_codex 基礎版兜底）──────────
_VENDORED_PROMPTS = Path(__file__).resolve().parent / "vendored" / "judge_prompts"

# 面向 → 深度 judge prompt（僅自動接面向對齊明確者；GEN-1 時效專用待時效子類再接）
_DEEP_BY_DIM: dict[str, str] = {
    "行程流程": "行程流程_G1G3.md",
}
# (面向, 欄位) → 深度 prompt（欄位級覆蓋優先於面向級）
_DEEP_BY_FIELD: dict[tuple[str, str], str] = {
    ("商品定位", "prod_name"): "商品名稱_judge_v2.md",
}


@lru_cache(maxsize=8)
def _read_deep(fname: str) -> str:
    fp = _VENDORED_PROMPTS / fname
    return fp.read_text(encoding="utf-8") if fp.exists() else ""


def _criteria_block(fd: dict) -> str:
    """field_codex 單欄 → 精簡判準塊（基礎版；非完整 build_field_prompt，避免帶入另一套輸出 schema）。"""
    return (
        f"【法典判準 · {fd['dimension']} / {fd['field']}】\n"
        f"治理原則：{fd['principle']}\n"
        f"條文(canon)：{fd['canon']}\n"
        f"✅ 允許：\n{_bullets(fd['allow'])}\n"
        f"❌ 禁止：\n{_bullets(fd['deny'])}\n"
        f"壞範例(Red Flag)：\n{_bullets(fd['bad_examples'])}\n"
        f"典型違規原因：{fd['violation_reason']}"
    )


def adequacy_criteria(dimension: str, field: str = "none") -> tuple[str, str]:
    """解析 adequacy 判準：深度 prompt（欄位級→面向級）→ field_codex 基礎版（精確欄位→面向首欄）→ 空。

    回 (criteria_text, source)；source ∈ deep:檔名 | codex:欄位 | codex_dim:面向 | none。
    「缺失先給基礎版本」：無深度 prompt 的面向自動回退 field_codex 法典生成 canon，全 8 面向皆有
    基礎判準；後續把深度 prompt 逐條補進 _DEEP_BY_DIM/_DEEP_BY_FIELD 即升級該面向/欄位。
    """
    fn = _DEEP_BY_FIELD.get((dimension, field))
    if fn and _read_deep(fn):
        return _read_deep(fn), f"deep:{fn}"
    fn = _DEEP_BY_DIM.get(dimension)
    if fn and _read_deep(fn):
        return _read_deep(fn), f"deep:{fn}"
    fd = get_field(dimension, field)
    if fd:
        return _criteria_block(fd), f"codex:{fd['field']}"
    nd = _norm_dim(dimension)
    dim_fields = [f for f in all_fields() if _norm_dim(f["dimension"]) == nd]
    if dim_fields:
        return _criteria_block(dim_fields[0]), f"codex_dim:{dimension}"
    return "", "none"

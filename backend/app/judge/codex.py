"""內容治理法典 — 機器檢查規則庫載入器（judge_rules.json）。

法典完整 SSOT = Google Sheets（58 欄位）；本檔提供 AI 法官 arbiter/diagnose
對 Phase1 30 條可機器檢查規則（R1-1~R5-5）的程式化查詢介面。
對應 specs/05-content-governance-codex.md。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_RULES_PATH = Path(__file__).resolve().parent / "judge_rules.json"


@lru_cache(maxsize=1)
def _codex() -> dict:
    with _RULES_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def all_rules() -> list[dict]:
    """回傳全部 30 條機器檢查規則。"""
    return _codex()["rules"]


def get_rule(rule_id: str) -> dict | None:
    """依 Rule ID（如 R1-1）取單條規則，找不到回 None。"""
    return next((r for r in all_rules() if r["rule_id"] == rule_id), None)


def rules_by_dimension(dimension: str) -> list[dict]:
    """取某 dimension 的所有規則（dimension 對齊 schema.Dimension）。"""
    return [r for r in all_rules() if r["dimension"] == dimension]


def severity_of(rule_id: str) -> str:
    """Rule ID → P1/P2/P3（依 risk_level High/Medium/Low 映射）。"""
    rule = get_rule(rule_id)
    if not rule:
        return "P3"
    return _codex()["risk_to_severity"].get(rule["risk_level"], "P3")


def contract_breach_rules() -> list[dict]:
    """承諾與SLA 類規則：事前查缺失→content_missing，事後已揭露未履約→contract_breach。"""
    return [r for r in all_rules() if r.get("contract_breach_applicable")]


# ── 欄位級法典（field_codex.json，60 欄位）+ judge prompt 生成器 ──────────
_FIELD_PATH = Path(__file__).resolve().parent / "field_codex.json"


@lru_cache(maxsize=1)
def _field_codex() -> dict:
    with _FIELD_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def all_fields() -> list[dict]:
    """回傳全部 60 欄位法典（dimension × field × canon/範例/機器規則）。"""
    return _field_codex()["fields"]


def get_field(dimension: str, field: str) -> dict | None:
    """依 面向 + 欄位名 取單欄法典（field 支援前綴比對，如『集合地點』）。"""
    for f in all_fields():
        if f["dimension"] == dimension and (f["field"] == field or f["field"].startswith(field)):
            return f
    return None


def _bullets(xs: list[str]) -> str:
    return "\n".join(f"- {x}" for x in xs) if xs else "（無）"


def build_field_prompt(fd: dict) -> str:
    """欄位法典 → 該欄位的 AI 法官 judge prompt（統一深度模板）。

    模板吸收 ProductContentAIChecker G1/GEN-1 的深度結構（角色→唯一判準→好壞範例→
    輸出 schema→防過擬鐵則）+ L2-L4 SD 的雙意見/防幻覺仲裁原則。
    """
    return f"""# AI 法官 · 欄位判決 Prompt — {fd['dimension']} / {fd['field']}

> 自動生成自內容治理法典（field_codex.json）。判準 SSOT = Google Sheets 法典。
> 角色：旅遊電商內容稽核員，**只依本欄位法典判決**，不得以法典未列出的理由扣分（裁判是法典執行器）。
> 只看本欄位原文 + 客服對話(ground truth)，**不採信客訴語氣**。

## 0. 判決目標（治理原則 · Why）
{fd['principle']}

## 1. 法典條文（Canon · 唯一判準）
{fd['canon']}

## 2. 允許 ✅ / 禁止 ❌
**✅ 允許**
{_bullets(fd['allow'])}

**❌ 禁止**
{_bullets(fd['deny'])}

## 3. 好範例（應判 Pass）
{_bullets(fd['good_examples'])}

## 4. 壞範例（Red Flag · 應判 Flag）
{_bullets(fd['bad_examples'])}

## 5. 可機器檢查線索
{_bullets(fd['machine_rule']) if isinstance(fd['machine_rule'], list) else fd['machine_rule']}

## 6. 判決輸出（嚴格 JSON，response_format=json_object）
{{
  "dimension": "{fd['dimension']}",
  "field": "{fd['field']}",
  "violation": true/false,
  "verdict": "real_config_issue | content_missing | content_unclear | contract_breach | customer_misread | escalate_ops",
  "evidence_quote": "命中問題的欄位原文片段；無則空字串",
  "ground_truth_quote": "客服對話中的標準答案/政策原文；無則空字串",
  "reason": "對照 canon 的違規理由（典型違規原因：{fd['violation_reason']}）",
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

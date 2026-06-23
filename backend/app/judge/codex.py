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

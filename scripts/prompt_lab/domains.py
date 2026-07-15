"""Prompt Lab 域配置读取与校验。域 JSON 是 C3～C6 生成计划的单一事实源。"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOMAINS_DIR = ROOT / "evals" / "prompt_lab" / "domains"


@lru_cache(maxsize=None)
def load_domain(domain: str) -> dict:
    slug = domain.lower().replace("-", "")
    path = DOMAINS_DIR / f"{slug}.json"
    if not path.exists():
        raise ValueError(f"尚未配置 domain={domain!r}：{path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("domain") != domain:
        raise ValueError(f"{path}: domain={data.get('domain')!r}，预期 {domain!r}")
    required = {
        "name", "l2", "negative_domains", "domain_boundaries",
        "l2_confusion_pairs", "policy_decisions", "generation",
    }
    missing = sorted(required - data.keys())
    if missing:
        raise ValueError(f"{path}: 缺少字段 {missing}")
    codes = [item["code"] for item in data["l2"]]
    if len(codes) != len(set(codes)) or not codes:
        raise ValueError(f"{path}: L2 code 为空或重复")
    if set(data["domain_boundaries"]) != set(codes):
        raise ValueError(f"{path}: domain_boundaries 必须覆盖全部 L2")
    for a, b in data["l2_confusion_pairs"]:
        if a not in codes or b not in codes or a == b:
            raise ValueError(f"{path}: 非法 l2_confusion_pair={[a, b]}")
    return data


def configured_domains() -> list[str]:
    return ["C-3", "C-4", "C-5", "C-6"]

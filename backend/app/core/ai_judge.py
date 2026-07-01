"""config/ai_judge 規則樹載入器：7 個 rule_C-*.json → 扁平 L3 判準（canon／正反例）。

config/ai_judge 為 AI 法官「問題分類規則樹」SSOT（C-1~C-7 七歸因域，遞迴 children，
L3 葉節點扛完整判準）。本模組負責讀檔、扁平化、建索引，供 arbiter／prejudge 在判決時
注入「厚判準」（canon + allow／forbid + 正反例），讓 LLM 依法典條文判 L3，而非僅憑名稱。

與 core/taxonomy.py 互補（非取代）：
- taxonomy：圈號↔code 橋接、候選域過濾（⑤⑦）、confidence 封頂、verdict→域收斂。
- 本模組：提供 L3 的「判準內容」（taxonomy 的 attribution_tree 只有結構與 severity，無 canon）。

domain ↔ 圈號 橋接讀 config/taxonomy/domains.json（單一真相源），與 rule 檔 _meta.domain 對齊。

快取：首次存取時 lazy 載入並快取於模組級變數；config 編輯後呼叫 reload() 重載。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# config/ 位於 repo 根：backend/app/core/ai_judge.py → parents[3] = repo root
_CONFIG_DIR = Path(__file__).resolve().parents[3] / "config"
_AI_JUDGE_DIR = _CONFIG_DIR / "ai_judge"


def _domains_file() -> Path:
    """domains.json 路徑：軸B 判決規則 config/ai_judge 優先，回退軸A config/taxonomy。

    （domains 屬軸B 判決層、已遷入 config/ai_judge；回退保相容未遷移環境。）
    """
    aj = _AI_JUDGE_DIR / "domains.json"
    return aj if aj.is_file() else _CONFIG_DIR / "taxonomy" / "domains.json"


# ── 模組級快取（lazy；reload() 清空重建）──
_l3_by_code: dict[str, dict[str, Any]] = {}
_l3_by_domain: dict[str, list[dict[str, Any]]] = {}
_domain_label: dict[str, str] = {}
_symbol2code: dict[str, str] = {}
_code2symbol: dict[str, str] = {}
# domains.json 完整 meta（code → {label_zh/verdict_default/intake_excluded…}）；漏斗 Stage1 選域用
_domain_meta: dict[str, dict[str, Any]] = {}
_domain_order: list[str] = []  # domains.json 原始順序（顯示穩定）
_loaded = False


def _flatten_l3(l1: dict[str, Any]) -> list[dict[str, Any]]:
    """單一 L1 域節點 → 其下所有 L3 葉節點（攤平帶 L1/L2 上下文）。

    Args:
        l1: rule 檔 tree[0]，含 code/domain/label/children(L2)。

    Returns:
        L3 dict 清單；每筆帶 l1_domain/l1_label/l2_code/l2_label + L3 本體判準欄位。
    """
    out: list[dict[str, Any]] = []
    l1_domain = l1.get("domain", "")
    l1_label = l1.get("label", "")
    for l2 in l1.get("children", []):
        l2_code = l2.get("code", "")
        l2_label = l2.get("label", "")
        for l3 in l2.get("children", []):
            if l3.get("level") != 3:
                continue
            out.append(
                {
                    "code": l3.get("code", ""),
                    "l1_domain": l1_domain,
                    "l1_label": l1_label,
                    "l2_code": l2_code,
                    "l2_label": l2_label,
                    "l3_label": l3.get("label", ""),
                    "meaning": l3.get("meaning", ""),
                    "canon": l3.get("canon", ""),
                    "allow": l3.get("allow", []),
                    "forbid": l3.get("forbid", []),
                    "positive_cases": l3.get("positive_cases", []),
                    "negative_cases": l3.get("negative_cases", []),
                    "verdict": l3.get("verdict", ""),
                    "rule": l3.get("rule", ""),
                }
            )
    return out


def _ensure_loaded() -> None:
    """lazy 載入 7 個 rule 檔 + domains.json，建索引並快取（冪等）。"""
    global _loaded
    if _loaded:
        return
    # 圈號↔code 橋接 + 域 meta 快取（domains.json 為 SSOT）
    domains = json.loads(_domains_file().read_text(encoding="utf-8")).get("items", [])
    for d in domains:
        code, sym = d.get("code", ""), d.get("symbol", "")
        if not code:
            continue
        if sym:
            _symbol2code[sym] = code
            _code2symbol[code] = sym
        _domain_meta[code] = d
        _domain_order.append(code)

    # 逐 rule 檔扁平化（檔名 rule_C-N.json，_meta.domain 為域 code）
    for rule_file in sorted(_AI_JUDGE_DIR.glob("rule_C-*.json")):
        data = json.loads(rule_file.read_text(encoding="utf-8"))
        tree = data.get("tree", [])
        if not tree:
            continue
        l1 = tree[0]
        domain = l1.get("domain", "")
        _domain_label[domain] = l1.get("label", domain)
        nodes = _flatten_l3(l1)
        _l3_by_domain.setdefault(domain, []).extend(nodes)
        for n in nodes:
            _l3_by_code[n["code"]] = n
    _loaded = True


def reload() -> None:
    """清空快取並重載（config/ai_judge 線上編輯後呼叫，使判決即時反映新判準）。"""
    global _loaded
    _l3_by_code.clear()
    _l3_by_domain.clear()
    _domain_label.clear()
    _symbol2code.clear()
    _code2symbol.clear()
    _domain_meta.clear()
    _domain_order.clear()
    _loaded = False
    _ensure_loaded()


def domain_code_for_symbol(symbol: str) -> str:
    """圈號（①~⑦）→ 域 code（content/supplier…）；未知回空字串。"""
    _ensure_loaded()
    return _symbol2code.get(symbol, "")


def symbol_for_domain_code(code: str) -> str:
    """域 code → 圈號（confidence_cap 等 taxonomy 函式吃圈號時轉換用）；未知回空字串。"""
    _ensure_loaded()
    return _code2symbol.get(code, "")


def domain_label(code: str) -> str:
    """域 code → 中文域名（顯示用）；未知回原 code。"""
    _ensure_loaded()
    return _domain_label.get(code, code)


def l3_by_code(code: str) -> dict[str, Any] | None:
    """C-code（如 C-1-1-4）→ 該 L3 完整判準節點；不存在回 None。"""
    _ensure_loaded()
    return _l3_by_code.get(code)


def valid_l3_codes() -> frozenset[str]:
    """全部合法 L3 C-code 集合（arbiter 白名單校驗 LLM 輸出用）。"""
    _ensure_loaded()
    return frozenset(_l3_by_code.keys())


def l3_nodes_for_domains(domain_codes: list[str]) -> list[dict[str, Any]]:
    """取指定歸因域（code 清單）底下所有 L3 節點（保序去重；空清單回全部）。

    Args:
        domain_codes: 域 code 清單（如 ["content", "supplier"]）；空清單→全 7 域。

    Returns:
        L3 判準節點清單（攤平）。
    """
    _ensure_loaded()
    codes = domain_codes or list(_l3_by_domain.keys())
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for dc in codes:
        for n in _l3_by_domain.get(dc, []):
            if n["code"] not in seen:
                seen.add(n["code"])
                out.append(n)
    return out


def domain_l2_labels(code: str) -> list[str]:
    """域 code → 其下 L2 面向中文 label 清單（保序去重）；供漏斗 Stage1 提示域涵蓋範圍。"""
    _ensure_loaded()
    out: list[str] = []
    seen: set[str] = set()
    for n in _l3_by_domain.get(code, []):
        lbl = n.get("l2_label", "")
        if lbl and lbl not in seen:
            seen.add(lbl)
            out.append(lbl)
    return out


def selectable_domains() -> list[dict[str, Any]]:
    """漏斗 Stage1 可選 L1 域清單（排除 intake_excluded＝⑤客服/⑦不可抗力，沿用既有預判候選語義）。

    每域附中文名 + L2 面向涵蓋 + 預設 verdict，讓 LLM 先在域層聚焦選擇（對抗一次攤平 60+ 條 L3
    的注意力稀釋）。不可抗力 / 客服營運不在預判選域，由 verdict（force_majeure / escalate_ops）逃生。

    Returns:
        [{code, label, l2_labels, verdict_default}]，依 domains.json 原始順序；不含 intake_excluded 域。
    """
    _ensure_loaded()
    out: list[dict[str, Any]] = []
    for code in _domain_order:
        meta = _domain_meta.get(code, {})
        if meta.get("intake_excluded"):
            continue
        out.append(
            {
                "code": code,
                "label": meta.get("label_zh", _domain_label.get(code, code)),
                "l2_labels": domain_l2_labels(code),
                "verdict_default": meta.get("verdict_default", ""),
            }
        )
    return out

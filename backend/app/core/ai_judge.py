"""config/ai_judge 規則樹載入器：rule_C-*.json → 扁平 L3 判準（canon／正反例）。

config/ai_judge 為 AI 法官「問題分類規則樹」SSOT（評論導向 6 歸因域，遞迴 children，L3 葉節點
扛完整判準）。本模組負責讀檔、扁平化、建索引，供 prejudge 在判決時注入「厚判準」（canon +
allow／forbid + 正反例），讓 LLM 依法典條文判 L3，而非僅憑名稱。

域清單（code / label / 順序 / 是否排除）直接自各 rule 檔推導——tree[0].domain 為域機器值、
tree[0].label 為中文域名、檔名 rule_C-N.json 排序為顯示順序、`_meta.intake_excluded=true` 則
不進預判候選域（可選 knob，預設 false）。不再需要獨立 domains.json（域資訊已在 rule 檔內，SSOT
唯一化，避免兩處維護漂移）。

快取：首次存取時 lazy 載入並快取於模組級變數；config 編輯後呼叫 reload() 重載。
"""

from __future__ import annotations

import json
from typing import Any

# config/ 目錄定位統一由 app.core.paths 提供（唯一數層數處），勿在此重算 parents[N]。
from app.core.paths import AI_JUDGE_DIR as _AI_JUDGE_DIR

# ── 模組級快取（lazy；reload() 清空重建）──
_l3_by_code: dict[str, dict[str, Any]] = {}
_l3_by_domain: dict[str, list[dict[str, Any]]] = {}
_domain_label: dict[str, str] = {}  # code → 中文域名（自 rule tree[0].label）
_domain_order: list[str] = []  # 域顯示順序（rule 檔名排序，穩定）
_domain_excluded: set[str] = set()  # _meta.intake_excluded=true 的域（不進預判候選）
_loaded = False


def _leaf_record(
    node: dict[str, Any], l1_domain: str, l1_label: str, *, l2_code: str, l2_label: str, l3_label: str
) -> dict[str, Any]:
    """組單一葉節點的攤平記錄（帶 L1/L2 上下文 + 判準欄位）。

    l2/l3_label 依葉所在層填：L3 葉→l2=父面向、l3=葉；L2 葉→l2=葉本身、l3 空；L1 葉→l2/l3 皆空。
    """
    return {
        "code": node.get("code", ""),
        "level": node.get("level"),
        "l1_domain": l1_domain,
        "l1_label": l1_label,
        "l2_code": l2_code,
        "l2_label": l2_label,
        "l3_label": l3_label,
        "meaning": node.get("meaning", ""),
        "canon": node.get("canon", ""),
        "allow": node.get("allow", []),
        "forbid": node.get("forbid", []),
        "positive_cases": node.get("positive_cases", []),
        "negative_cases": node.get("negative_cases", []),
        "rule": node.get("rule", ""),
    }


def _flatten_l3(l1: dict[str, Any]) -> list[dict[str, Any]]:
    """單一 L1 域節點 → 其下所有「葉節點」（變深度：葉可在 L1/L2/L3，無 children 者即葉）。

    Args:
        l1: rule 檔 tree[0]，含 code/domain/label/children(L2)。

    Returns:
        葉節點攤平清單；每筆帶 l1_domain/l1_label/l2_code/l2_label/l3_label + 判準欄位 + level。
        L2 葉：l2 填自身、l3_label 空；L1 葉（域即歸因）：l2/l3 皆空。
    """
    out: list[dict[str, Any]] = []
    l1_domain = l1.get("domain", "")
    l1_label = l1.get("label", "")
    l1_children = l1.get("children") or []
    if not l1_children:  # L1 本身即葉（域即最終歸因層）
        out.append(_leaf_record(l1, l1_domain, l1_label, l2_code="", l2_label="", l3_label=""))
        return out
    for l2 in l1_children:
        l2_code = l2.get("code", "")
        l2_label = l2.get("label", "")
        l2_children = l2.get("children") or []
        if not l2_children:  # L2 葉（面向即最終歸因層，不細分 L3）
            out.append(_leaf_record(l2, l1_domain, l1_label, l2_code=l2_code, l2_label=l2_label, l3_label=""))
            continue
        for l3 in l2_children:  # L3 葉（最深層，恆為葉）
            out.append(
                _leaf_record(
                    l3, l1_domain, l1_label, l2_code=l2_code, l2_label=l2_label, l3_label=l3.get("label", "")
                )
            )
    return out


def _ensure_loaded() -> None:
    """lazy 載入所有 rule_C-*.json，建索引 + 域 meta 並快取（冪等）。

    域清單／順序／label／排除旗標全自 rule 檔推導（檔名排序＝顯示順序，tree[0] 帶 domain/label，
    _meta.intake_excluded 決定是否進預判候選）。不讀 domains.json。
    """
    global _loaded
    if _loaded:
        return
    for rule_file in sorted(_AI_JUDGE_DIR.glob("rule_C-*.json")):
        data = json.loads(rule_file.read_text(encoding="utf-8"))
        tree = data.get("tree", [])
        if not tree:
            continue
        l1 = tree[0]
        domain = l1.get("domain", "")
        if not domain:
            continue
        _domain_label[domain] = l1.get("label", domain)
        if domain not in _domain_order:
            _domain_order.append(domain)
        if data.get("_meta", {}).get("intake_excluded"):
            _domain_excluded.add(domain)
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
    _domain_order.clear()
    _domain_excluded.clear()
    _loaded = False
    _ensure_loaded()


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
        domain_codes: 域 code 清單（如 ["content", "supplier"]）；空清單→全部域。

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
    """漏斗 Stage1 可選 L1 域清單（排除 _meta.intake_excluded 的域）。

    每域附中文名 + L2 面向涵蓋，讓 LLM 先在域層聚焦選擇（對抗一次攤平數十條 L3 的注意力稀釋）。
    intake_excluded 域（若有設）不進預判選域，逃生改走 polarity + L1→L3 歸因。

    Returns:
        [{code, label, l2_labels}]，依 rule 檔名排序；不含 intake_excluded 域。
    """
    _ensure_loaded()
    out: list[dict[str, Any]] = []
    for code in _domain_order:
        if code in _domain_excluded:
            continue
        out.append(
            {
                "code": code,
                "label": _domain_label.get(code, code),
                "l2_labels": domain_l2_labels(code),
            }
        )
    return out

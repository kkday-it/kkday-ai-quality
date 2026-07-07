"""AI 法官「問題分類規則樹」載入器：judge_rule_versions（DB）→ 扁平 L3 判準（canon／正反例）。

規則樹 SSOT＝DB judge_rule_versions active 版（評論導向 6 歸因域，遞迴 children，L3 葉節點扛完整
判準），config/ai_judge/rule_C-*.json 為初始 seed / 無 DB 版本時的 fallback。本模組讀 DB、扁平化、
建索引，供 prejudge 在判決時注入「厚判準」（canon + allow／forbid + 正反例），讓 LLM 依法典條文
判 L3。改讀 DB（原直讀檔）與 RuleManager／product_vertical 一致，使線上編輯／恢復默認即時生效。

域清單（code / label / 順序 / 是否排除）自各 rule active content 推導——tree[0].domain 為域機器值、
tree[0].label 為中文域名、db.RULE_CODES 的 C-N 數字序為顯示順序、`_meta.intake_excluded=true` 則
不進預判候選域（可選 knob，預設 false）；`_meta.recommended_action` 為域→行動 SSOT。

快取：首次存取時 lazy 載入並快取於模組級變數；規則寫入（存檔／恢復默認／恢復版本）後呼叫 reload() 重載。
"""

from __future__ import annotations

from typing import Any

# ── 模組級快取（lazy；reload() 清空重建）──
_l3_by_code: dict[str, dict[str, Any]] = {}
_l3_by_domain: dict[str, list[dict[str, Any]]] = {}
_domain_label: dict[str, str] = {}  # code → 中文域名（自 rule tree[0].label）
_domain_order: list[str] = []  # 域顯示順序（rule 檔名排序，穩定）
_domain_excluded: set[str] = set()  # _meta.intake_excluded=true 的域（不進預判候選）
_domain_action: dict[str, str] = {}  # code → recommended_action（自 rule _meta.recommended_action）
_domain_owner: dict[
    str, str
] = {}  # code → 負責單位（自 rule _meta.owner_role；值待業務填，空則不顯示）
_cascade: list[
    dict[str, Any]
] = []  # 前端級聯選項（巢狀 {value,label,children}；L1 value=域 code，L2/L3 value=C-code）
_path_label: dict[
    str, str
] = {}  # value（域 code / C-code）→ 可讀路徑「L1 › L2 › L3」（供標真值評分 prompt）
_l1_judgment: dict[
    str, dict[str, Any]
] = {}  # domain 機器值 → L1 域判準（canon/allow/forbid/正反例）；cascade Stage A 界線
_l2_judgment: dict[
    str, dict[str, Any]
] = {}  # L2 C-code（有 L3 者）→ L2 面向判準；cascade Stage B 界線
_loaded = False


def _leaf_record(
    node: dict[str, Any],
    l1_domain: str,
    l1_label: str,
    *,
    l2_code: str,
    l2_label: str,
    l3_label: str,
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
        "canon": node.get("canon", ""),
        "allow": node.get("allow", []),
        "forbid": node.get("forbid", []),
        "positive_cases": node.get("positive_cases", []),
        "negative_cases": node.get("negative_cases", []),
    }


def _branch_judgment(node: dict[str, Any]) -> dict[str, Any]:
    """抽取分支節點（L1 域／L2 面向）判準五欄 + code/label；供 cascade 分層界線注入（Stage A/B）。

    分支節點（有 children）的判準在 schema 放寬前不存在（僅葉扛判準）；放寬後 L1/L2 可帶界線判準，
    本函式集中抽取，缺欄以空值回退（分支判準為選填，未填時 canon 為空、prompt 端自動略過）。
    """
    return {
        "code": node.get("code", ""),
        "label": node.get("label", ""),
        "canon": node.get("canon", ""),
        "allow": node.get("allow", []),
        "forbid": node.get("forbid", []),
        "positive_cases": node.get("positive_cases", []),
        "negative_cases": node.get("negative_cases", []),
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
            out.append(
                _leaf_record(
                    l2, l1_domain, l1_label, l2_code=l2_code, l2_label=l2_label, l3_label=""
                )
            )
            continue
        for l3 in l2_children:  # L3 葉（最深層，恆為葉）
            out.append(
                _leaf_record(
                    l3,
                    l1_domain,
                    l1_label,
                    l2_code=l2_code,
                    l2_label=l2_label,
                    l3_label=l3.get("label", ""),
                )
            )
    return out


def _ensure_loaded() -> None:
    """lazy 載入 C-N 域規則，建索引 + 域 meta 並快取（冪等）。

    **SSOT＝DB judge_rule_versions active 版**（缺版本才回退 config/ai_judge seed 檔），與 RuleManager／
    product_vertical 一致改讀 DB——使線上編輯／恢復默認後呼叫 reload() 即時反映於判決，不再直讀檔造成
    「UI 改了判決卻沒變」。域清單依 db.RULE_CODES 的 C-N（按數字序＝顯示順序），tree[0] 帶 domain/label，
    _meta.intake_excluded 決定是否進預判候選、_meta.recommended_action 為域→行動 SSOT。
    """
    global _loaded
    if _loaded:
        return
    from app.core import db  # lazy：避免 import-time 拉 sqlalchemy；db 不 import 本模組故無循環

    domain_codes = sorted(
        (c for c in db.RULE_CODES if c.startswith("C-")),
        key=lambda c: int(c.split("-", 1)[1]),
    )
    for code in domain_codes:
        data = db.get_rule_active(code)
        if data is None:
            try:
                data = db.default_rule_content(code)  # 無 DB 版本 → 回退 seed 檔
            except FileNotFoundError:
                continue
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
        _meta = data.get("_meta", {})
        if _meta.get("intake_excluded"):
            _domain_excluded.add(domain)
        action = _meta.get("recommended_action")
        if action:  # 域→建議行動（SSOT 內聚於各 rule _meta，取代 prejudge 舊硬編碼 dict）
            _domain_action[domain] = action
        owner = _meta.get("owner_role")
        if owner:  # 域→負責單位（SSOT 於各 rule _meta.owner_role；值待業務填，填後 re-seed 即流通）
            _domain_owner[domain] = owner
        nodes = _flatten_l3(l1)
        _l3_by_domain.setdefault(domain, []).extend(nodes)
        for n in nodes:
            _l3_by_code[n["code"]] = n
        # 分支判準（cascade 分層界線）：L1 域判準 by domain；L2 面向判準 by C-code（僅有 L3 之 L2；
        # L2 葉本身已在 _l3_by_code）。判準為選填，未填時 canon 空、prompt 端略過不影響現行行為。
        _l1_judgment[domain] = _branch_judgment(l1)
        for l2 in l1.get("children") or []:
            if l2.get("children"):  # L2 分支（其下有 L3）；L2 葉走 _l3_by_code
                _l2_judgment[l2.get("code", "")] = _branch_judgment(l2)
        _cascade.append(_build_cascade(l1, [], root=True))
    _loaded = True


def _build_cascade(node: dict[str, Any], ancestors: list[str], *, root: bool) -> dict[str, Any]:
    """遞迴組級聯節點 {value,label,children}，並登記每個 value 的完整路徑 label（供標真值評分 prompt）。

    root（L1 域）value＝域機器值（domain，對齊 selectable_domains / true_label 儲存）；L2/L3 value＝C-code。
    """
    value = node.get("domain", "") if root else node.get("code", "")
    label = node.get("label", "") or value
    path = [*ancestors, label]
    if value:
        _path_label[value] = " › ".join(path)
    out: dict[str, Any] = {"value": value, "label": label}
    children = [_build_cascade(ch, path, root=False) for ch in (node.get("children") or [])]
    if children:
        out["children"] = children
    return out


def reload() -> None:
    """清空快取並重載（config/ai_judge 線上編輯後呼叫，使判決即時反映新判準）。"""
    global _loaded
    _l3_by_code.clear()
    _l3_by_domain.clear()
    _domain_label.clear()
    _domain_order.clear()
    _domain_excluded.clear()
    _domain_action.clear()
    _domain_owner.clear()
    _cascade.clear()
    _path_label.clear()
    _l1_judgment.clear()
    _l2_judgment.clear()
    _loaded = False
    _ensure_loaded()


def cascade_tree() -> list[dict[str, Any]]:
    """完整歸因分類級聯樹（巢狀 {value,label,children}）——供前端標真值級聯選 L1→L2→L3 葉。"""
    _ensure_loaded()
    return _cascade


def path_label(code: str) -> str:
    """歸因 value（域 code / C-code）→ 可讀完整路徑「L1 › L2 › L3」；未知回空字串。"""
    _ensure_loaded()
    return _path_label.get(code, "")


def domain_label(code: str) -> str:
    """域 code → 中文域名（顯示用）；未知回原 code。"""
    _ensure_loaded()
    return _domain_label.get(code, code)


def domain_action(code: str) -> str:
    """域 code → recommended_action（自 rule _meta.recommended_action）；未設回 escalate_ux。

    取代 prejudge 舊 _DOMAIN_ACTION 硬編碼（曾用已廢域名 order/platform/cs，導致現行域靜默失準）。
    """
    _ensure_loaded()
    return _domain_action.get(code, "escalate_ux")


def domain_owner(code: str) -> str:
    """域 code → 負責單位（自 rule _meta.owner_role，如 AM / 客服）；未設回空字串（前端空則不顯示）。

    值為業務配置（禁自創）：於各 rule_C-N.json `_meta.owner_role` 填入、經 RuleManager 恢復默認
    re-seed 後即流通到判決 judges；未填時 owner 恆空，前端不顯示負責單位標籤。
    """
    _ensure_loaded()
    return _domain_owner.get(code, "")


def l3_by_code(code: str) -> dict[str, Any] | None:
    """C-code（如 C-1-1-4）→ 該 L3 完整判準節點；不存在回 None。"""
    _ensure_loaded()
    return _l3_by_code.get(code)


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


def l1_judgment(domain: str) -> dict[str, Any]:
    """域機器值（content/supplier…）→ L1 域判準（canon/allow/forbid/正反例 + label）；未設回空 dict。

    供 cascade Stage A 選域時注入域界線（取代 global_rule.gates 平行 SSOT）。判準為選填，
    分支未填判準時回傳 canon 為空的 dict，prompt 端據 canon 是否非空決定注入與否。
    """
    _ensure_loaded()
    return _l1_judgment.get(domain, {})


def l2_judgment(code: str) -> dict[str, Any]:
    """L2 面向 C-code（如 C-1-2）→ L2 判準；未設（或該 L2 為葉、判準已在 l3_by_code）回空 dict。

    供 cascade Stage B 在選中域內先以 L2 canon 定調、再列該 L2 下 L3 canon 之分組式目錄。
    """
    _ensure_loaded()
    return _l2_judgment.get(code, {})

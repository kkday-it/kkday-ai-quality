"""AI 法官「問題分類結構」載入器：prompt_source.structure()（7 支 prompt md 派生）→ 扁平索引。

**結構 SSOT＝docs/prompts/prompts/*.md**（Prompt-as-Source 架構）：域機器值來自 prompt 檔名尾綴
（content/quality/supplier/platform/service/customer）、L2 面向（facets）來自各域 prompt 的
`<facet_catalog>`「■ CODE LABEL」行解析、域中文名／action／owner 來自 `config/ai_judge/domains.json`
（唯一不可從 prompt 推導的域層業務 metadata）。原 JSON 規則樹（judge_rule_versions 的 C-1~C-6）已退役
——判準文字（canon/allow/forbid/正反例）現為 prompt 本身內容，供 LLM 直接讀，不再由本模組拼字串二次
注入 prompt；本模組僅建「分類結構」索引（域/面向 code↔label、級聯樹、顯示序），供 15 處消費端
（歸因列表級聯篩選、judgments 顯示、標真值 cascader、_l2_label_map 等）查詢。

深度：僅 L1（域）→ L2（面向）二層（L3 已隨 legacy 引擎退役，`prejudge_depth=l2` 下判決本就只到 L2）。
`l1_judgment`/`l2_judgment` 的 canon/allow/forbid/正反例欄恆為空（判準已在 prompt，非本模組職責）——
唯一殘留消費端為 legacy 判決路徑（待刪），刪除前呼叫不報錯、僅取不到文字。

快取：首次存取時 lazy 載入並快取於模組級變數；reload() 連動清空 prompt_source 快取後重建，保證
「prompt 存檔／恢復默認／批次判決入口重載」時分類結構與判決 prompt 同步刷新（不會一邊新一邊舊）。
"""

from __future__ import annotations

from typing import Any

# ── 模組級快取（lazy；reload() 清空重建）──
_l3_by_code: dict[str, dict[str, Any]] = {}
_l3_by_domain: dict[str, list[dict[str, Any]]] = {}
_domain_label: dict[str, str] = {}  # domain 機器值 → 中文域名（自 domains.json label）
_domain_order: list[str] = []  # 域顯示順序（依 prompt_source.structure() 序，即 prompt 檔名序）
_domain_excluded: set[str] = (
    set()
)  # domains.json intake_excluded=true 的域（不進預判候選；目前皆未設）
_domain_action: dict[str, str] = {}  # domain → recommended_action（自 domains.json action）
_domain_owner: dict[str, str] = {}  # domain → 負責單位（自 domains.json owner；空則前端不顯示）
_cascade: list[
    dict[str, Any]
] = []  # 前端級聯選項（巢狀 {value,label,children}；L1 value=域機器值，L2 value=面向 code）
_path_label: dict[
    str, str
] = {}  # value（域機器值 / 面向 code）→ 可讀路徑「L1 › L2」（供標真值評分 prompt）
_l1_judgment: dict[
    str, dict[str, Any]
] = {}  # domain 機器值 → L1 域判準殼（canon 恆空；legacy 消費端相容用）
_l2_judgment: dict[
    str, dict[str, Any]
] = {}  # 恆空（原「L2 有 L3 子節點」的分支判準；現無 L3，皆為葉，見 _l3_by_code）
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
    """組單一面向葉節點的攤平記錄（帶 L1/L2 上下文；判準欄位恆空，見模組 docstring）。

    l3_label 參數保留（欄位形狀相容 legacy 消費端）但呼叫端恆傳空字串——無 L3 深度。
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
    """組域層（L1）判準殼:{code,label} + 恆空判準五欄；供 `_l1_judgment` 建值（見模組 docstring）。"""
    return {
        "code": node.get("code", ""),
        "label": node.get("label", ""),
        "canon": node.get("canon", ""),
        "allow": node.get("allow", []),
        "forbid": node.get("forbid", []),
        "positive_cases": node.get("positive_cases", []),
        "negative_cases": node.get("negative_cases", []),
    }


def _ensure_loaded() -> None:
    """lazy 載入六域分類結構（自 `prompt_source.structure()` 派生），建索引 + 域 meta 並快取（冪等）。

    每域固定二層（domain → facets，無 L3）：facets 直接視為葉節點（`_leaf_record` 沿用既有欄位形狀，
    canon/allow/forbid/正反例欄留空——判準已在 prompt md 本身，非本模組職責）。`_build_cascade` 沿用
    既有遞迴演算法，餵入 `{domain, label, children:[{code,label},...]}` 的合成節點即可產生同形狀級聯樹。
    """
    global _loaded
    if _loaded:
        return
    from app.judge import prompt_source  # lazy：prompt_source 不 import 本模組，無循環

    for d in prompt_source.structure()["domains"]:
        domain = d.get("domain", "")
        if not domain:
            continue
        label = d.get("domain_label") or domain
        _domain_label[domain] = label
        if domain not in _domain_order:
            _domain_order.append(domain)
        if d.get("intake_excluded"):  # domains.json 選填 knob（目前皆未設，六域全進預判候選）
            _domain_excluded.add(domain)
        action = d.get("action")
        if action:  # 域→建議行動（SSOT＝domains.json，取代 prejudge 舊硬編碼 dict）
            _domain_action[domain] = action
        owner = d.get("owner")
        if owner:  # 域→負責單位（SSOT＝domains.json；值待業務填，填後即流通）
            _domain_owner[domain] = owner
        facets = d.get("facets") or []
        for f in facets:
            leaf = _leaf_record(
                {"code": f.get("code", ""), "level": 2},
                domain,
                label,
                l2_code=f.get("code", ""),
                l2_label=f.get("label", ""),
                l3_label="",
            )
            _l3_by_domain.setdefault(domain, []).append(leaf)
            _l3_by_code[leaf["code"]] = leaf
        _l1_judgment[domain] = _branch_judgment({"code": domain, "label": label})
        _cascade.append(
            _build_cascade(
                {
                    "domain": domain,
                    "label": label,
                    "children": [
                        {"code": f.get("code", ""), "label": f.get("label", "")} for f in facets
                    ],
                },
                [],
                root=True,
            )
        )
    _loaded = True


def _build_cascade(node: dict[str, Any], ancestors: list[str], *, root: bool) -> dict[str, Any]:
    """遞迴組級聯節點 {value,label,children}，並登記每個 value 的完整路徑 label（供標真值評分 prompt）。

    root（L1 域）value＝域機器值（domain，對齊 selectable_domains / true_label 儲存）；L2 value＝面向 code。
    現輸入固定二層（domain→facets），但演算法本身仍為通用遞迴、非本次改動範圍。
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
    """清空快取並重載（prompt 存檔／恢復默認／批次判決入口後呼叫，使分類結構即時反映新版）。

    連動清空 `prompt_source` 的 md 解析快取（結構派生自它）——保證兩者一起刷新，不會結構已重載但
    prompt 文字仍是舊快取（曾是缺口：prejudge_batch._reload_judge_rules 只 reload ai_judge，未連動
    prompt_source，本函式起自洽，呼叫端毋須額外記得）。
    """
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
    from app.judge import prompt_source

    prompt_source.reload()
    _ensure_loaded()


def cascade_tree() -> list[dict[str, Any]]:
    """完整歸因分類級聯樹（巢狀 {value,label,children}）——供前端標真值級聯選 L1→L2 葉。"""
    _ensure_loaded()
    return _cascade


def path_label(code: str) -> str:
    """歸因 value（域機器值 / 面向 code）→ 可讀完整路徑「L1 › L2」；未知回空字串。"""
    _ensure_loaded()
    return _path_label.get(code, "")


def domain_label(code: str) -> str:
    """域 code → 中文域名（顯示用）；未知回原 code。"""
    _ensure_loaded()
    return _domain_label.get(code, code)


def domain_action(code: str) -> str:
    """域 code → recommended_action（自 config/ai_judge/domains.json）；未設回 escalate_ux。

    取代 prejudge 舊 _DOMAIN_ACTION 硬編碼（曾用已廢域名 order/platform/cs，導致現行域靜默失準）。
    """
    _ensure_loaded()
    return _domain_action.get(code, "escalate_ux")


def domain_owner(code: str) -> str:
    """域 code → 負責單位（自 config/ai_judge/domains.json owner，如 AM / 客服）；未設回空字串（前端空則不顯示）。

    值為業務配置（禁自創）：於 domains.json 該域條目填入 owner，reload 後即流通到判決 judges；
    未填時 owner 恆空，前端不顯示負責單位標籤。
    """
    _ensure_loaded()
    return _domain_owner.get(code, "")


def l3_by_code(code: str) -> dict[str, Any] | None:
    """面向 code（如 C-3-1）→ 該面向攤平記錄（l3_label 恆空，無 L3）；不存在回 None。"""
    _ensure_loaded()
    return _l3_by_code.get(code)


def l3_nodes_for_domains(domain_codes: list[str]) -> list[dict[str, Any]]:
    """取指定歸因域（code 清單）底下所有 L2 面向節點（保序去重；空清單回全部）。

    Args:
        domain_codes: 域機器值清單（如 ["content", "supplier"]）；空清單→全部域。

    Returns:
        面向攤平清單（每筆 l3_label 恆空，無 L3）。
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
    """漏斗 Stage1 可選 L1 域清單（排除 domains.json intake_excluded 的域；目前六域皆未設）。

    每域附中文名 + L2 面向涵蓋，讓 LLM 先在域層聚焦選擇（對抗一次攤平數十條面向的注意力稀釋）。

    Returns:
        [{code, label, l2_labels}]，依 prompt 檔名序；不含 intake_excluded 域。
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
    """域機器值（content/supplier…）→ L1 域判準殼（code/label + 恆空 canon/allow/forbid/正反例）。

    ⚠️ canon 恆為空字串——判準文字已在 prompt md 本身（LLM 直接讀），本函式僅為 legacy 判決路徑
    （待 A3 刪除）保留相容形狀，刪除前呼叫不報錯、僅取不到判準文字（該路徑本就不是 live 引擎）。
    """
    _ensure_loaded()
    return _l1_judgment.get(domain, {})


def l2_judgment(code: str) -> dict[str, Any]:
    """恆回空 dict（原「L2 有 L3 子節點」的分支判準查詢；現無 L3，L2 皆為葉，見 l3_by_code）。

    保留純為 legacy 判決路徑（待 A3 刪除）相容,呼叫不報錯。
    """
    _ensure_loaded()
    return _l2_judgment.get(code, {})

"""AI 法官「問題分類結構」載入器：prompt_source.structure()（7 支 prompt md 派生）→ 扁平索引。

**結構 SSOT＝prompts/*.md 的 `## Taxonomy`**（Prompt-as-Source）：域機器值來自 prompt 檔名尾綴
（content/quality/supplier/platform/service/customer）、分類樹（facets/層級）＋域中文名／action／owner
／evidence_gated 全來自各域 prompt 的 `## Taxonomy` root。判準
例句（✅❌/正反例）為 prompt `<domain_boundary>` prose，供 LLM 直接讀，本模組不攜帶；僅建「分類結構」
索引（域/面向 code↔label、級聯樹、evidence_gated），供歸因列表篩選、attributions 顯示、`_l2_label_map`
等消費端查詢。

深度：僅 L1（域）→ L2（面向）二層（初判引擎 prompt_pack 只判到 L2）。

快取：首次存取時 lazy 載入並快取於模組級變數；reload() 連動清空 prompt_source 快取後重建，保證
「prompt 存檔／恢復默認／批次初判入口重載」時分類結構與初判 prompt 同步刷新（不會一邊新一邊舊）。
"""

from __future__ import annotations

from typing import Any

# ── 模組級快取（lazy；reload() 清空重建）──
_l2_by_code: dict[str, dict[str, Any]] = {}
_l2_by_domain: dict[str, list[dict[str, Any]]] = {}
_domain_label: dict[str, str] = {}  # domain 機器值 → 中文域名（自 `## Taxonomy` root label）
_domain_action: dict[str, str] = {}  # domain → recommended_action（自 `## Taxonomy` root action）
_domain_owner: dict[
    str, str
] = {}  # domain → 負責單位（自 `## Taxonomy` root owner；空則前端不顯示）
_domain_evidence_gated: set[str] = (
    set()
)  # 需外部訂單佐證才可高信心的域（自 `## Taxonomy` root evidence_gated）
_cascade: list[
    dict[str, Any]
] = []  # 前端級聯選項（巢狀 {value,label,children}；L1 value=域機器值，L2 value=面向 code）
_loaded = False


def _leaf_record(
    node: dict[str, Any],
    l1_domain: str,
    l1_label: str,
    *,
    l2_code: str,
    l2_label: str,
) -> dict[str, Any]:
    """組單一面向葉節點的攤平記錄（帶 L1/L2 上下文）。"""
    return {
        "code": node.get("code", ""),
        "level": node.get("level"),
        "l1_domain": l1_domain,
        "l1_label": l1_label,
        "l2_code": l2_code,
        "l2_label": l2_label,
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
        action = d.get("action")
        if action:  # 域→建議行動（SSOT＝`## Taxonomy` root action）
            _domain_action[domain] = action
        owner = d.get("owner")
        if owner:  # 域→負責單位（SSOT＝`## Taxonomy` root；值待業務填，填後即流通）
            _domain_owner[domain] = owner
        if d.get("evidence_gated"):  # 域→需外部訂單佐證（自 `## Taxonomy` root）
            _domain_evidence_gated.add(domain)
        facets = d.get("facets") or []
        for f in facets:
            leaf = _leaf_record(
                {"code": f.get("code", ""), "level": 2},
                domain,
                label,
                l2_code=f.get("code", ""),
                l2_label=f.get("label", ""),
            )
            _l2_by_domain.setdefault(domain, []).append(leaf)
            _l2_by_code[leaf["code"]] = leaf
        _cascade.append(
            _build_cascade(
                {
                    "domain": domain,
                    "label": label,
                    "children": [
                        {"code": f.get("code", ""), "label": f.get("label", "")} for f in facets
                    ],
                },
                root=True,
            )
        )
    _loaded = True


def _build_cascade(node: dict[str, Any], *, root: bool) -> dict[str, Any]:
    """遞迴組級聯節點 {value,label,children}（供前端歸因分類 cascader：歸因列表篩選）。

    root（L1 域）value＝域機器值（domain）；L2 value＝面向 code。現輸入固定二層（domain→facets）。
    """
    value = node.get("domain", "") if root else node.get("code", "")
    label = node.get("label", "") or value
    out: dict[str, Any] = {"value": value, "label": label}
    children = [_build_cascade(ch, root=False) for ch in (node.get("children") or [])]
    if children:
        out["children"] = children
    return out


def reload() -> None:
    """清空快取並重載（prompt 存檔／恢復默認／批次初判入口後呼叫，使分類結構即時反映新版）。

    連動清空 `prompt_source` 的 md 解析快取（結構派生自它）——保證兩者一起刷新，不會結構已重載但
    prompt 文字仍是舊快取（曾是缺口：prejudge_batch._reload_judge_rules 只 reload ai_judge，未連動
    prompt_source，本函式起自洽，呼叫端毋須額外記得）。
    """
    global _loaded
    _l2_by_code.clear()
    _l2_by_domain.clear()
    _domain_label.clear()
    _domain_action.clear()
    _domain_owner.clear()
    _domain_evidence_gated.clear()
    _cascade.clear()
    _loaded = False
    from app.judge import prompt_source

    prompt_source.reload()
    _ensure_loaded()


def cascade_tree() -> list[dict[str, Any]]:
    """完整歸因分類級聯樹（巢狀 {value,label,children}）——供前端 L1→L2 級聯選
    （歸因列表篩選選域與面向）。"""
    _ensure_loaded()
    return _cascade


def domain_label(code: str) -> str:
    """域 code → 中文域名（顯示用）；未知回原 code。"""
    _ensure_loaded()
    return _domain_label.get(code, code)


def evidence_gated_domains() -> frozenset[str]:
    """需外部訂單佐證才可高信心的域機器值集合（自各域 `## Taxonomy` root 的 evidence_gated）。

    該域是否需佐證＝該域自己的語義，寫在自己 prompt。
    """
    _ensure_loaded()
    return frozenset(_domain_evidence_gated)


def domain_action(code: str) -> str:
    """域 code → recommended_action（自各域 `## Taxonomy` root action）；未設回 escalate_ux。"""
    _ensure_loaded()
    return _domain_action.get(code, "escalate_ux")


def domain_owner(code: str) -> str:
    """域 code → 負責單位（自各域 `## Taxonomy` root owner，如 AM / 客服）；未設回空字串（前端空則不顯示）。

    值為業務配置（禁自創）：於該域 prompt `## Taxonomy` root 填入 owner，reload 後即流通到初判 judges；
    未填時 owner 恆空，前端不顯示負責單位標籤。
    """
    _ensure_loaded()
    return _domain_owner.get(code, "")


def l2_by_code(code: str) -> dict[str, Any] | None:
    """面向 code（如 C-3-1）→ 該面向攤平記錄；不存在回 None。"""
    _ensure_loaded()
    return _l2_by_code.get(code)


def l2_nodes_for_domains(domain_codes: list[str]) -> list[dict[str, Any]]:
    """取指定歸因域（code 清單）底下所有 L2 面向節點（保序去重；空清單回全部）。

    Args:
        domain_codes: 域機器值清單（如 ["content", "supplier"]）；空清單→全部域。

    Returns:
        面向攤平清單。
    """
    _ensure_loaded()
    codes = domain_codes or list(_l2_by_domain.keys())
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for dc in codes:
        for n in _l2_by_domain.get(dc, []):
            if n["code"] not in seen:
                seen.add(n["code"])
                out.append(n)
    return out

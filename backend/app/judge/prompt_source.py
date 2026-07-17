"""初判 Prompt 唯一真相源載入層（Prompt-as-Source 架構核心）。

架構反轉：初判 prompt 不再由「JSON 規則樹 → 槽位渲染」派生，而是 **7 支完整 md（prompts/
*.md：00_polarity + 01_C-1~06_C-6）＝唯一真相源**——人直接編輯、git 版控、PR 審查。

三溫層：
- **檔案**（prompts/*.md）＝git 版控默認 seed（容器 dev 掛 ./docs、prod COPY prompts）。
- **DB**（judge_rule_versions 的 prompt_polarity + prompt_C-1~6，content={"_meta":..., "text": md 全文}）
  ＝線上熱編 active 版 + 完整歷史（RuleManager「初判 Prompt」分組編輯）。
- **本模組** load()：DB active 優先 → 檔案 fallback；parse 三節（System/User/Schema）；模組級 lazy cache，
  規則寫入後 reload() 清快取（比照 ai_judge loader 慣例）。

md 格式契約（引擎按此解析）：
    # {標題}
    ## System   → ``` fence 內＝system prompt 全文（judge 人設 + facet_catalog 例句 + domain_boundary；模型面向）
    ## User     → ``` fence 內＝user 模板；{TEXT} 必有；域 prompt 另需 {POLARITY}（模型面向）
    ## Taxonomy → ```json fence 內＝域分類樹 root（機器面向：分類唯一源，不送 LLM）；域 prompt 專有、polarity 無
    ## Schema   → ```json fence 內＝該支輸出 JSON Schema；域 prompt 的 attributions[].l2_code enum 由 `## Taxonomy` 派生注入

分類結構：structure() 派生——域機器值 ← prompt 檔名尾綴（content/quality/supplier/
platform/service/customer）；分類樹＋域中文名/action/owner/evidence_gated ← 各域 `## Taxonomy` root。
ai_judge loader 讀 structure() 建索引;Schema l2_code enum 由 `## Taxonomy` 派生（不手寫、無 drift）。
"""

from __future__ import annotations

import json
import re
from typing import Any

# ── prompt_id ↔ rule_code 對照（SSOT）──
# prompt_id＝prompts/{id}.md 檔名（去副檔名）；rule_code＝judge_rule_versions 主鍵。
# 域 prompt 的 rule_code = prompt_C-N，對應歸因分類樹 rule_code C-N（同 N，取樹域機器值用）。
_PROMPT_RULE: dict[str, str] = {
    "00_polarity": "prompt_polarity",
    "01_C-1_content": "prompt_C-1",
    "02_C-2_quality": "prompt_C-2",
    "03_C-3_supplier": "prompt_C-3",
    "04_C-4_platform": "prompt_C-4",
    "05_C-5_service": "prompt_C-5",
    "06_C-6_customer": "prompt_C-6",
}
_RULE_PROMPT: dict[str, str] = {v: k for k, v in _PROMPT_RULE.items()}

PROMPT_IDS: tuple[str, ...] = tuple(_PROMPT_RULE.keys())
POLARITY_ID = "00_polarity"
DOMAIN_PROMPT_IDS: tuple[str, ...] = tuple(pid for pid in PROMPT_IDS if pid != POLARITY_ID)
PROMPT_RULE_CODES: tuple[str, ...] = tuple(_PROMPT_RULE.values())

# 左選單顯示 label（list_rule_meta 對 prompt_* 無 tree，回退 _meta.label）。
# 純中文名，不重複帶 C-N 後綴——RuleManager 選單已另外把 code（C-1 等）當前綴徽章顯示，
# label 若又帶「C-1」會與前綴徽章重複（「C-1　商品內容 C-1」）。
_PROMPT_LABEL: dict[str, str] = {
    "00_polarity": "情緒傾向",
    "01_C-1_content": "商品內容",
    "02_C-2_quality": "商品品質",
    "03_C-3_supplier": "供應商履約",
    "04_C-4_platform": "平台與系統",
    "05_C-5_service": "客服營運",
    "06_C-6_customer": "理解期待",
}

# ── 模組級解析快取（lazy；reload() 清空）──
_cache: dict[str, dict[str, Any]] = {}


# ─────────────────────────── 對照查詢 ───────────────────────────
def prompt_id_for_rule(rule_code: str) -> str | None:
    """rule_code（prompt_*）→ prompt_id；非 prompt rule 回 None。"""
    return _RULE_PROMPT.get(rule_code)


def rule_code_for_prompt(prompt_id: str) -> str | None:
    """prompt_id → rule_code（prompt_*）；未知回 None。"""
    return _PROMPT_RULE.get(prompt_id)


# ─────────────────────────── md 解析 ───────────────────────────
def _extract_title(text: str) -> str:
    """取首個 H1（`# ...`）標題純文字；`## ...` 不算（需 `#` 後緊接空白）。"""
    m = re.search(r"(?m)^#[ \t]+(.+?)[ \t]*$", text)
    return m.group(1).strip() if m else ""


def _extract_fenced(text: str, heading: str) -> str:
    """取 `## {heading}` 之後第一個 ``` fenced block 內容（去圍欄；```json 等語言標記亦相容）。

    system prompt 內部僅用 XML 標籤、不含三連反引號，故非貪婪匹配到第一個閉圍欄即該節內容。
    缺 heading 或缺圍欄拋 ValueError（fail-loud，配合存檔 validate 擋 + 載入 fail-loud）。
    """
    hm = re.search(rf"(?m)^##[ \t]+{re.escape(heading)}[ \t]*$", text)
    if not hm:
        raise ValueError(f"缺少 `## {heading}` 區塊")
    rest = text[hm.end() :]
    fm = re.search(r"(?ms)^```[^\n]*\n(.*?)^```[ \t]*$", rest)
    if not fm:
        raise ValueError(f"`## {heading}` 區塊後缺少 ``` 圍欄程式區塊")
    return fm.group(1).rstrip("\n")


def parse_md(text: str) -> dict[str, Any]:
    """解析單支 prompt md → {title, system, user_template, schema}。

    Args:
        text: 完整 md 全文（含 H1 + System/User/Schema 三節圍欄）。

    Returns:
        {"title": str, "system": str, "user_template": str, "schema": dict, "taxonomy": dict|None}。

    Raises:
        ValueError: 缺 H1／缺任一節／圍欄缺失／Schema 非合法 JSON。
    """
    title = _extract_title(text)
    if not title:
        raise ValueError("缺少 H1 標題（# ...）")
    system = _extract_fenced(text, "System")
    user_template = _extract_fenced(text, "User")
    schema_raw = _extract_fenced(text, "Schema")
    try:
        schema = json.loads(schema_raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"Schema JSON 解析失敗：{e}") from None
    if not isinstance(schema, dict):
        raise ValueError("Schema 須為 JSON object")
    return {
        "title": title,
        "system": system,
        "user_template": user_template,
        "schema": schema,
        "taxonomy": _parse_taxonomy_section(
            text
        ),  # 域 prompt 有 `## Taxonomy` 節；polarity 回 None
    }


# ─────────────────────────── 載入（DB-first → 檔案 fallback）───────────────────────────
def _raw_text(prompt_id: str) -> str:
    """取某 prompt 的 md 原文：DB active（content["text"]）優先，缺則 prompts/{id}.md。

    Raises:
        FileNotFoundError: DB 無 active 版且檔案不存在（引擎 fail-loud，不靜默走空 prompt）。
    """
    rule_code = _PROMPT_RULE[prompt_id]
    from app.core import db  # lazy：避免 import-time 拉 sqlalchemy（db 不 import 本模組故無循環）

    content = db.get_rule_active(rule_code)
    if content and isinstance(content.get("text"), str) and content["text"].strip():
        return content["text"]
    from app.core.paths import PROMPTS_DIR

    path = PROMPTS_DIR / f"{prompt_id}.md"
    if not path.exists():
        raise FileNotFoundError(f"prompt 檔不存在且 DB 無 active 版：{path}")
    return path.read_text(encoding="utf-8")


def load(
    prompt_id: str,
    versions: dict[str, int] | None = None,
    drafts: dict[str, str] | None = None,
) -> dict[str, Any]:
    """取某 prompt 解析結果（DB active 優先→檔案 fallback）；lazy 模組級 cache。

    Args:
        prompt_id: PROMPT_IDS 之一（如 "00_polarity" / "03_C-3_supplier"）。
        versions: {rule_code: 指定歷史版本號}（版本選擇功能）。命中時讀
            `db.get_rule_version(rule_code, version)` 那個特定版本的內容，**不寫入 `_cache`**
            （指定版本只服務本次呼叫，不可污染其他並發正式初判/沙盒 job）。
        drafts: {rule_code: md 全文}（草稿測試功能，僅沙盒路徑使用）。命中時直接解析該
            全文（不觸 DB），同樣**不寫入 `_cache`**；同 rule_code 與 versions 並存時
            **drafts 優先**（草稿本就基於某版本編輯，測的是草稿內容）。

    Returns:
        {"title", "system", "user_template", "schema"}。

    Raises:
        ValueError: 未知 prompt_id、md 解析失敗，或指定的 versions 版本號不存在。
        FileNotFoundError: 無 DB 版且無檔案（僅無 versions/drafts 命中時才會走到此路徑）。
    """
    if prompt_id not in _PROMPT_RULE:
        raise ValueError(f"未知 prompt_id：{prompt_id}")
    rule_code = _PROMPT_RULE[prompt_id]
    draft_text = drafts.get(rule_code) if drafts else None
    pinned_version = versions.get(rule_code) if versions else None
    if draft_text is None and pinned_version is None and prompt_id in _cache:
        return _cache[prompt_id]
    if draft_text is not None:
        raw_text = draft_text
    elif pinned_version is not None:
        from app.core import db  # lazy：同 `_raw_text` 避免 import-time 拉 sqlalchemy

        content = db.get_rule_version(rule_code, pinned_version)
        if not content or not isinstance(content.get("text"), str) or not content["text"].strip():
            raise ValueError(f"{rule_code} 無版本 {pinned_version}")
        raw_text = content["text"]
    else:
        raw_text = _raw_text(prompt_id)
    parsed = parse_md(raw_text)
    if _is_domain(prompt_id):
        root = parsed["taxonomy"]
        if root is None:
            raise ValueError(f"域 prompt 缺 `## Taxonomy` 節：{prompt_id}")
        # 域 prompt 的 Schema l2_code enum 由 `## Taxonomy` 派生注入（prompt 不手寫 code 清單、零 drift）
        codes = [f["code"] for f in _flatten_taxonomy(root)]
        _inject_derived_enum(parsed["schema"], codes)
    if draft_text is not None or pinned_version is not None:
        return parsed  # 草稿/指定版本路徑：不快取
    _cache[prompt_id] = parsed
    return parsed


def reload() -> None:
    """清 prompt 解析快取（RuleManager 存檔 / seed / 恢復默認後呼叫，使初判即時採新版）。"""
    _cache.clear()


# ─────────────────────────── 默認 seed content（供 rule_versions）───────────────────────────
def default_prompt_content(rule_code: str) -> dict[str, Any]:
    """rule_code（prompt_*）→ 默認 seed content（DB 版本化格式）。

    讀 prompts/{id}.md 原文，包成 {"_meta": {label, kind:"prompt"}, "text": md 全文}。
    _meta.label 供 list_rule_meta 左選單顯示（prompt_* 無 tree，回退 _meta.label）。

    Raises:
        ValueError: 非 prompt rule_code。
        FileNotFoundError: 對應 md 檔不存在（比照其他 rule 默認檔缺失行為）。
    """
    prompt_id = _RULE_PROMPT.get(rule_code)
    if prompt_id is None:
        raise ValueError(f"非 prompt rule_code：{rule_code}")
    from app.core.paths import PROMPTS_DIR

    md = (PROMPTS_DIR / f"{prompt_id}.md").read_text(encoding="utf-8")  # 不存在→FileNotFoundError
    parse_md(md)  # 早驗：默認檔本身壞掉時 seed 立即失敗，不把壞 prompt 種進 DB
    return {
        "_meta": {"label": _PROMPT_LABEL.get(prompt_id, prompt_id), "kind": "prompt"},
        "text": md,
    }


# ─────────────────────────── 結構（各域 prompt `## Taxonomy` 派生）───────────────────────────
def _is_domain(prompt_id: str) -> bool:
    """是否域 prompt（有 facets 結構;polarity 無）。"""
    return _PROMPT_RULE.get(prompt_id, "").startswith("prompt_C-")


def _domain_of(prompt_id: str) -> str:
    """域 prompt → 域機器值（＝檔名尾綴,如 03_C-3_supplier→supplier;04_C-4_platform→platform）;polarity 回空。"""
    return prompt_id.split("_", 2)[2] if _is_domain(prompt_id) else ""


def _parse_taxonomy_section(text: str) -> dict[str, Any] | None:
    """解析 `## Taxonomy` 節（```json 圍欄）→ 域分類樹 root dict；無此節（如 polarity）回 None。

    root＝域節點 `{code(域機器值), label, action, owner, evidence_gated, children:[...]}`；children 為
    facet 節點（可再巢狀 children＝可變深度）。**分類的類別＋層級＋域 metadata 唯一源**——enum/篩選樹/
    驗證皆由此派生，程式碼零 taxonomy 假設，prompt 改即全換。與 `## Schema` 同屬機器契約、同以 json 圍欄
    解析（不再嵌於 System 供 LLM，見 decision_process 明示模型改讀 facet_catalog）。有節但格式錯（圍欄缺/
    非合法 JSON/root 形狀錯）拋 ValueError（fail-loud）。
    """
    if not re.search(r"(?m)^##[ \t]+Taxonomy[ \t]*$", text):
        return None
    try:
        root = json.loads(_extract_fenced(text, "Taxonomy"))
    except json.JSONDecodeError as e:
        raise ValueError(f"`## Taxonomy` 非合法 JSON：{e}") from None
    if (
        not isinstance(root, dict)
        or not root.get("code")
        or not isinstance(root.get("children"), list)
    ):
        raise ValueError("`## Taxonomy` root 須為 {code, label, children:[...]} 物件")
    return root


def _flatten_taxonomy(root: dict[str, Any]) -> list[dict[str, str]]:
    """域分類樹 → 攤平 facet 清單 [{code,label}]（不含 root 域節點；深度優先保序去重，支援可變深度）。"""
    out: list[dict[str, str]] = []
    seen: set[str] = set()

    def walk(nodes: list[dict[str, Any]]) -> None:
        for n in nodes:
            code = str(n.get("code", "")).strip()
            if code and code not in seen:
                seen.add(code)
                out.append({"code": code, "label": str(n.get("label", "")).strip()})
            walk(n.get("children") or [])

    walk(root.get("children") or [])
    return out


def _inject_derived_enum(schema: dict[str, Any], codes: list[str]) -> None:
    """把 `## Taxonomy` 派生的 facet codes 注入 schema 的 attributions[].l2_code.enum（prompt 不手寫 code 清單）。"""
    try:
        schema["properties"]["attributions"]["items"]["properties"]["l2_code"]["enum"] = codes
    except (KeyError, TypeError):
        raise ValueError("域 prompt schema 缺 attributions[].l2_code（供派生 enum 注入）") from None


def schema_l2_enum_for(prompt_id: str) -> set[str]:
    """某域 prompt（active/檔案）派生後 Schema 的 l2_code enum 集合（供 pytest）。"""
    enum = load(prompt_id)["schema"]["properties"]["attributions"]["items"]["properties"][
        "l2_code"
    ]["enum"]
    return set(enum)


def structure() -> dict[str, Any]:
    """彙整 6 支域 prompt 的分類結構——供 ai_judge loader 建索引。

    每支域 prompt 的 ``## Taxonomy`` 區塊（JSON）為唯一源：域節點（機器值/中文名/action/owner/
    evidence_gated）+ facet 子樹（可變深度）。程式碼零 taxonomy 假設，prompt 改 → reload → 全換。

    Returns:
        {"domains": [{domain, domain_label, action, owner, evidence_gated, facets:[{code,label}], tree}, ...]}。
    """
    out: list[dict[str, Any]] = []
    for pid in DOMAIN_PROMPT_IDS:
        root = load(pid)["taxonomy"]  # 域 prompt load 已驗證非 None
        out.append(
            {
                "domain": _domain_of(pid),
                "domain_label": str(root.get("label", "")),
                "action": str(root.get("action", "")),
                "owner": str(root.get("owner", "")),
                "evidence_gated": bool(root.get("evidence_gated", False)),
                "facets": _flatten_taxonomy(root),
                "tree": root.get("children") or [],
            }
        )
    return {"domains": out}


def validate(text: str, prompt_id: str) -> None:
    """存檔前驗證 prompt md（存檔閘門）；不過拋 ValueError。

    驗：三節可解析 + Schema 合法 JSON Schema + User 含 {TEXT}（域另需 {POLARITY}）
    + 域 prompt 的 ``## Taxonomy`` 可解析、至少一個 facet、schema 有 l2_code 供派生 enum 注入。
    （enum 由 taxonomy 派生，無 drift 之虞，故無 facet==enum 護欄。）

    Args:
        text: 待存 md 全文。
        prompt_id: 對應 PROMPT_IDS 之一。

    Raises:
        ValueError: 任一驗證項不過。
    """
    if prompt_id not in _PROMPT_RULE:
        raise ValueError(f"未知 prompt_id：{prompt_id}")
    parsed = parse_md(text)  # 缺節/壞 JSON → ValueError

    import jsonschema

    try:
        jsonschema.Draft202012Validator.check_schema(parsed["schema"])
    except jsonschema.exceptions.SchemaError as e:
        raise ValueError(f"Schema 不是合法 JSON Schema：{e.message}") from None

    if "{TEXT}" not in parsed["user_template"]:
        raise ValueError("User 模板必須含 {TEXT} 佔位符")

    if not _is_domain(prompt_id):  # polarity：無域、無 taxonomy,止於前述通用驗證
        return

    if "{POLARITY}" not in parsed["user_template"]:
        raise ValueError("域 prompt 的 User 模板必須含 {POLARITY} 佔位符")

    root = parsed["taxonomy"]  # 有節但壞 JSON → parse_md 已拋；此處驗「有節」
    if root is None:
        raise ValueError("域 prompt 缺 `## Taxonomy` 節")
    if not _flatten_taxonomy(root):
        raise ValueError("`## Taxonomy` 至少需一個 facet 節點")
    _inject_derived_enum(parsed["schema"], [])  # 只驗 schema 有 l2_code 路徑（enum 由 load 派生）

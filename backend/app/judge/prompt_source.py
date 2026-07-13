"""判決 Prompt 唯一真相源載入層（Prompt-as-Source 架構核心）。

架構反轉：判決 prompt 不再由「JSON 規則樹 → 槽位渲染」派生，而是 **7 支完整 md（docs/prompts/prompts/
*.md：00_polarity + 01_C-1~06_C-6）＝唯一真相源**——人直接編輯、git 版控、PR 審查。

三溫層：
- **檔案**（docs/prompts/prompts/*.md）＝git 版控默認 seed（容器 dev 掛 ./docs、prod COPY docs/prompts）。
- **DB**（judge_rule_versions 的 prompt_polarity + prompt_C-1~6，content={"_meta":..., "text": md 全文}）
  ＝線上熱編 active 版 + 完整歷史（RuleManager「初判 Prompt」分組編輯）。
- **本模組** load()：DB active 優先 → 檔案 fallback；parse 三節（System/User/Schema）；模組級 lazy cache，
  規則寫入後 reload() 清快取（比照 ai_judge loader 慣例）。

md 格式契約（現有 7 檔已符合，引擎按此解析）：
    # {標題}
    ## System   → ``` fence 內＝system prompt 全文
    ## User     → ``` fence 內＝user 模板；{TEXT} 必有；域 prompt 另需 {POLARITY}
    ## Schema   → ```json fence 內＝該支輸出 JSON Schema；域 prompt 的 attributions[].l2_code enum

Drift 雙護欄（樹↔prompt 唯二耦合點）：validate() 驗「域 prompt Schema 的 l2_code enum ⊆ 樹該域 L2
codes」（存檔時）；pytest 另驗「六支 enum 聯集 == 樹全 L2 codes」（單邊改動即紅）。
"""

from __future__ import annotations

import json
import re
from typing import Any

# ── prompt_id ↔ rule_code 對照（SSOT）──
# prompt_id＝docs/prompts/prompts/{id}.md 檔名（去副檔名）；rule_code＝judge_rule_versions 主鍵。
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
_PROMPT_LABEL: dict[str, str] = {
    "00_polarity": "情緒傾向（Step1）",
    "01_C-1_content": "商品內容 C-1",
    "02_C-2_quality": "商品品質 C-2",
    "03_C-3_supplier": "供應商履約 C-3",
    "04_C-4_platform": "平台與系統 C-4",
    "05_C-5_service": "客服營運 C-5",
    "06_C-6_customer": "理解期待 C-6",
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


def is_prompt_rule(rule_code: str) -> bool:
    """rule_code 是否為初判 Prompt（prompt_*）。"""
    return rule_code in _RULE_PROMPT


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
        {"title": str, "system": str, "user_template": str, "schema": dict}。

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
    return {"title": title, "system": system, "user_template": user_template, "schema": schema}


# ─────────────────────────── 載入（DB-first → 檔案 fallback）───────────────────────────
def _raw_text(prompt_id: str) -> str:
    """取某 prompt 的 md 原文：DB active（content["text"]）優先，缺則 docs/prompts/prompts/{id}.md。

    Raises:
        FileNotFoundError: DB 無 active 版且檔案不存在（引擎 fail-loud，不靜默走空 prompt）。
    """
    rule_code = _PROMPT_RULE[prompt_id]
    from app.core import db  # lazy：避免 import-time 拉 sqlalchemy（db 不 import 本模組故無循環）

    content = db.get_rule_active(rule_code)
    if content and isinstance(content.get("text"), str) and content["text"].strip():
        return content["text"]
    from app.core.paths import DOCS_PROMPTS_DIR

    path = DOCS_PROMPTS_DIR / f"{prompt_id}.md"
    if not path.exists():
        raise FileNotFoundError(f"prompt 檔不存在且 DB 無 active 版：{path}")
    return path.read_text(encoding="utf-8")


def load(prompt_id: str) -> dict[str, Any]:
    """取某 prompt 解析結果（DB active 優先→檔案 fallback）；lazy 模組級 cache。

    Args:
        prompt_id: PROMPT_IDS 之一（如 "00_polarity" / "03_C-3_supplier"）。

    Returns:
        {"title", "system", "user_template", "schema"}。

    Raises:
        ValueError: 未知 prompt_id 或 md 解析失敗。
        FileNotFoundError: 無 DB 版且無檔案。
    """
    if prompt_id in _cache:
        return _cache[prompt_id]
    if prompt_id not in _PROMPT_RULE:
        raise ValueError(f"未知 prompt_id：{prompt_id}")
    parsed = parse_md(_raw_text(prompt_id))
    _cache[prompt_id] = parsed
    return parsed


def reload() -> None:
    """清 prompt 解析快取（RuleManager 存檔 / seed / 恢復默認後呼叫，使判決即時採新版）。"""
    _cache.clear()


# ─────────────────────────── 默認 seed content（供 rule_versions）───────────────────────────
def default_prompt_content(rule_code: str) -> dict[str, Any]:
    """rule_code（prompt_*）→ 默認 seed content（DB 版本化格式）。

    讀 docs/prompts/prompts/{id}.md 原文，包成 {"_meta": {label, kind:"prompt"}, "text": md 全文}。
    _meta.label 供 list_rule_meta 左選單顯示（prompt_* 無 tree，回退 _meta.label）。

    Raises:
        ValueError: 非 prompt rule_code。
        FileNotFoundError: 對應 md 檔不存在（比照其他 rule 默認檔缺失行為）。
    """
    prompt_id = _RULE_PROMPT.get(rule_code)
    if prompt_id is None:
        raise ValueError(f"非 prompt rule_code：{rule_code}")
    from app.core.paths import DOCS_PROMPTS_DIR

    md = (DOCS_PROMPTS_DIR / f"{prompt_id}.md").read_text(
        encoding="utf-8"
    )  # 不存在→FileNotFoundError
    parse_md(md)  # 早驗：默認檔本身壞掉時 seed 立即失敗，不把壞 prompt 種進 DB
    return {
        "_meta": {"label": _PROMPT_LABEL.get(prompt_id, prompt_id), "kind": "prompt"},
        "text": md,
    }


# ─────────────────────────── 驗證（存檔閘門 + drift 護欄）───────────────────────────
def _domain_of(prompt_id: str) -> str | None:
    """域 prompt → 對應歸因分類域機器值（自 C-N 樹 tree[0].domain）；polarity 回 None。

    不硬編碼域機器值——由樹 SSOT 推導，樹改域名時自動跟隨（drift 護欄據此對照 enum）。
    """
    rule_code = _PROMPT_RULE.get(prompt_id, "")
    if not rule_code.startswith("prompt_C-"):
        return None
    taxo_code = rule_code[len("prompt_") :]  # prompt_C-3 → C-3
    from app.core import db

    content = db.get_rule_active(taxo_code)
    if content is None:
        try:
            content = db.default_rule_content(taxo_code)
        except FileNotFoundError:
            return None
    tree = (content or {}).get("tree") or []
    return tree[0].get("domain") if tree else None


def _schema_l2_enum(schema: dict[str, Any]) -> set[str]:
    """從域 prompt schema 取 attributions[].items.l2_code.enum 集合。"""
    try:
        enum = schema["properties"]["attributions"]["items"]["properties"]["l2_code"]["enum"]
    except (KeyError, TypeError):
        raise ValueError("域 prompt schema 缺 attributions[].l2_code.enum") from None
    if not isinstance(enum, list) or not all(isinstance(x, str) for x in enum):
        raise ValueError("attributions[].l2_code.enum 須為字串清單")
    return set(enum)


def tree_l2_codes(domain: str) -> set[str]:
    """樹某域所有 L2 code 集合（自 ai_judge.l3_nodes_for_domains 攤平節點的 l2_code）。"""
    from app.core import ai_judge

    return {n["l2_code"] for n in ai_judge.l3_nodes_for_domains([domain]) if n.get("l2_code")}


def schema_l2_enum_for(prompt_id: str) -> set[str]:
    """某域 prompt（active/檔案）Schema 的 l2_code enum 集合（供 pytest 聯集護欄）。"""
    return _schema_l2_enum(load(prompt_id)["schema"])


def validate(text: str, prompt_id: str) -> None:
    """存檔前驗證 prompt md（存檔閘門）；不過拋 ValueError。

    驗：三節可解析 + Schema 合法 JSON Schema + User 含 {TEXT}（域另需 {POLARITY}）
    + 域 prompt Schema 的 l2_code enum ⊆ 樹該域 L2 codes（drift 護欄唯一耦合點）。

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

    domain = _domain_of(prompt_id)
    if domain is None:  # polarity：無域、無 l2 enum，止於前述通用驗證
        return

    if "{POLARITY}" not in parsed["user_template"]:
        raise ValueError("域 prompt 的 User 模板必須含 {POLARITY} 佔位符")

    enum = _schema_l2_enum(parsed["schema"])
    tree_codes = tree_l2_codes(domain)
    extra = enum - tree_codes
    if extra:
        raise ValueError(
            f"Schema l2_code enum 含樹外 code {sorted(extra)}"
            f"（域 {domain} 樹 L2={sorted(tree_codes)}）——先改樹或修正 enum"
        )

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


def _extract_fenced_opt(text: str, heading: str) -> str | None:
    """取 `## {heading}` 之後首個圍欄內容;該節不存在回 None（選填節,如域 prompt 的 ## Meta）。"""
    if not re.search(rf"(?m)^##[ \t]+{re.escape(heading)}[ \t]*$", text):
        return None
    return _extract_fenced(text, heading)


def parse_md(text: str) -> dict[str, Any]:
    """解析單支 prompt md → {title, system, user_template, schema, meta}。

    Args:
        text: 完整 md 全文（含 H1 + System/User/Schema 三節圍欄;域 prompt 另含選填 ## Meta 結構節）。

    Returns:
        {"title": str, "system": str, "user_template": str, "schema": dict, "meta": dict|None}。
        meta＝## Meta 節的 JSON（域 prompt 的分類結構:{domain, domain_label, action, owner, facets:[{code,label}]}）;
        polarity prompt 無此節 → meta=None。

    Raises:
        ValueError: 缺 H1／缺任一必節／圍欄缺失／Schema 或 Meta 非合法 JSON。
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
    meta_raw = _extract_fenced_opt(text, "Meta")
    meta: dict[str, Any] | None = None
    if meta_raw is not None:
        try:
            meta = json.loads(meta_raw)
        except json.JSONDecodeError as e:
            raise ValueError(f"Meta JSON 解析失敗：{e}") from None
        if not isinstance(meta, dict):
            raise ValueError("Meta 須為 JSON object")
    return {
        "title": title,
        "system": system,
        "user_template": user_template,
        "schema": schema,
        "meta": meta,
    }


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


# ─────────────────────────── 結構（## Meta）+ 驗證（自洽 drift 護欄）───────────────────────────
def _is_domain(prompt_id: str) -> bool:
    """是否域 prompt（有 facets 結構;polarity 無）。"""
    return _PROMPT_RULE.get(prompt_id, "").startswith("prompt_C-")


def _schema_l2_enum(schema: dict[str, Any]) -> set[str]:
    """從域 prompt schema 取 attributions[].items.l2_code.enum 集合。"""
    try:
        enum = schema["properties"]["attributions"]["items"]["properties"]["l2_code"]["enum"]
    except (KeyError, TypeError):
        raise ValueError("域 prompt schema 缺 attributions[].l2_code.enum") from None
    if not isinstance(enum, list) or not all(isinstance(x, str) for x in enum):
        raise ValueError("attributions[].l2_code.enum 須為字串清單")
    return set(enum)


def _meta_facet_codes(meta: dict[str, Any] | None) -> set[str]:
    """Meta.facets 的 code 集合。"""
    facets = (meta or {}).get("facets") or []
    return {f.get("code", "") for f in facets if isinstance(f, dict) and f.get("code")}


def schema_l2_enum_for(prompt_id: str) -> set[str]:
    """某域 prompt（active/檔案）Schema 的 l2_code enum 集合（供 pytest）。"""
    return _schema_l2_enum(load(prompt_id)["schema"])


def structure() -> dict[str, Any]:
    """彙整 6 支域 prompt 的分類結構（## Meta）——供 ai_judge loader 建索引,**取代 DB 樹**。

    Returns:
        {"domains": [{domain, domain_label, action, owner, facets:[{code,label}]}, ...]}（依 prompt_id 序;
        polarity 不含）。domain＝機器值（content/supplier…）;facets＝該域 L2 面向 code→label。
    """
    out: list[dict[str, Any]] = []
    for pid in DOMAIN_PROMPT_IDS:
        m = load(pid).get("meta") or {}
        out.append(
            {
                "domain": m.get("domain", ""),
                "domain_label": m.get("domain_label", ""),
                "action": m.get("action", ""),
                "owner": m.get("owner", ""),
                "facets": [
                    {"code": f.get("code", ""), "label": f.get("label", "")}
                    for f in (m.get("facets") or [])
                    if isinstance(f, dict) and f.get("code")
                ],
            }
        )
    return {"domains": out}


def validate(text: str, prompt_id: str) -> None:
    """存檔前驗證 prompt md（存檔閘門）；不過拋 ValueError。

    驗：三節可解析 + Schema 合法 JSON Schema + User 含 {TEXT}（域另需 {POLARITY}）
    + **自洽 drift 護欄**:域 prompt 的 Schema l2_code enum **==** Meta.facets codes（判準 schema 與
    結構註冊表同源自洽,取代原「enum ⊆ 樹」——樹已退役,結構在 Meta）。

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

    if not _is_domain(prompt_id):  # polarity：無域、無 facets,止於前述通用驗證
        return

    if "{POLARITY}" not in parsed["user_template"]:
        raise ValueError("域 prompt 的 User 模板必須含 {POLARITY} 佔位符")

    meta = parsed.get("meta")
    if not isinstance(meta, dict) or not meta.get("domain"):
        raise ValueError("域 prompt 缺 ## Meta 結構節（需含 domain + facets）")

    enum = _schema_l2_enum(parsed["schema"])
    facet_codes = _meta_facet_codes(meta)
    if enum != facet_codes:
        raise ValueError(
            "Schema l2_code enum 與 Meta.facets 不一致:"
            f"僅 enum={sorted(enum - facet_codes)}｜僅 Meta={sorted(facet_codes - enum)}"
        )

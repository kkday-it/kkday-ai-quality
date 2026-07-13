"""Judge Prompt Markdown 解析器（PRD §10.1）——把 `01_C-1_content.md` 拆成可執行三段。

契約：讀取 `## System`、`## User`、`## Schema` 各自的**第一個** fenced code block；
於 user 段替換 `{POLARITY}`／`{TEXT}`。缺段、JSON Schema 非法或占位符缺失一律**立即失敗**，
不得靜默用空 prompt（PRD §10.1）。

純模組：不呼叫 API，可離線單測。用 str.replace（非 .format）做占位符替換——prompt 內含大量
JSON 大括號，.format 會炸。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

# 占位符：user 段必須含這兩個，缺一即失敗（避免送出未填充的模板）
POLARITY_PLACEHOLDER = "{POLARITY}"
TEXT_PLACEHOLDER = "{TEXT}"

_HEADER_RE = re.compile(
    r"^##\s+([A-Za-z][\w -]*?)\s*$"
)  # 任一 level-2 header（System/User/Schema…）
_FENCE_RE = re.compile(r"^```")  # fence 開/關；開頭可帶語言標籤（```json）


class PromptParseError(ValueError):
    """Prompt 解析或校驗失敗（缺段／無 fenced block／JSON 非法／占位符缺失）。"""


def _first_fenced_block(lines: list[str], header_idx: int) -> str:
    """從 header 行之後找第一個 fenced code block，回傳其內容（不含 fence 行）。

    Args:
        lines: 全文行清單。
        header_idx: 該 `## X` header 所在行索引。

    Returns:
        fenced block 內文（保留內部換行；末尾不含結尾 fence）。

    Raises:
        PromptParseError: header 後（下一個 header 前）找不到成對 fenced block。
    """
    i = header_idx + 1
    # 找開啟 fence（略過 header 與 fence 之間的空行/說明文字）；遇到下一個 header 即中止
    while i < len(lines):
        if _HEADER_RE.match(lines[i].strip()):
            raise PromptParseError(
                f"'{lines[header_idx].strip()}' 段內未見 fenced code block"
            )
        if _FENCE_RE.match(lines[i].lstrip()):
            break
        i += 1
    else:
        raise PromptParseError(
            f"'{lines[header_idx].strip()}' 段後找不到 fenced code block"
        )
    # 收集直到關閉 fence
    body: list[str] = []
    j = i + 1
    while j < len(lines):
        if _FENCE_RE.match(lines[j].lstrip()):
            return "\n".join(body)
        body.append(lines[j])
        j += 1
    raise PromptParseError(
        f"'{lines[header_idx].strip()}' 段的 fenced block 未見結尾 ```"
    )


@dataclass(frozen=True)
class ParsedPrompt:
    """解析後的 judge prompt：三段內文 + schema dict + 溯源（sha256/version）。"""

    system: str
    user_template: str
    schema: dict
    raw: str
    sha256: str
    version: str
    path: str | None = None

    def render_user(self, polarity: str, text: str) -> str:
        """把 {POLARITY}／{TEXT} 填入 user 模板（str.replace，先極性後文本）。

        text 內任何大括號皆視為字面量（不做 format），Prompt Injection 內容僅作待判資料。
        """
        return self.user_template.replace(POLARITY_PLACEHOLDER, polarity).replace(
            TEXT_PLACEHOLDER, text
        )

    def schema_l2_enum(self) -> list[str]:
        """從 schema 取出 l2_code 的 enum 清單（SSOT 對齊守衛用；找不到回空清單）。"""
        found: list[str] = []

        def walk(node: object) -> None:
            if isinstance(node, dict):
                props = node.get("properties")
                if isinstance(props, dict) and "l2_code" in props:
                    enum = props["l2_code"].get("enum")
                    if isinstance(enum, list):
                        found.extend(str(x) for x in enum)
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)

        walk(self.schema)
        return found


def parse_prompt_text(
    raw: str, *, version: str = "", path: str | None = None
) -> ParsedPrompt:
    """解析 judge prompt 全文為 ParsedPrompt，並執行 §10.1 全部 fail-loud 校驗。

    Args:
        raw: prompt Markdown 全文。
        version: prompt 版本標籤（記入 JudgeRunResult.prompt_version）；空則用 path stem。
        path: 來源路徑（僅溯源記錄）。

    Raises:
        PromptParseError: 缺任一段、fenced block 缺失、Schema 非 JSON / 非合法 JSON Schema、
            或 user 段缺 {POLARITY}／{TEXT}。
    """
    lines = raw.splitlines()
    headers: dict[str, int] = {}
    for idx, line in enumerate(lines):
        m = _HEADER_RE.match(line.strip())
        if m:
            headers.setdefault(m.group(1).capitalize(), idx)  # 只取首個同名 header
    for need in ("System", "User", "Schema"):
        if need not in headers:
            raise PromptParseError(f"缺少必要段落 '## {need}'")

    system = _first_fenced_block(lines, headers["System"])
    user_template = _first_fenced_block(lines, headers["User"])
    schema_block = _first_fenced_block(lines, headers["Schema"])

    # Schema 必須是合法 JSON 且為合法 JSON Schema
    try:
        schema = json.loads(schema_block)
    except json.JSONDecodeError as e:
        raise PromptParseError(f"## Schema 區塊非合法 JSON：{e}") from e
    if not isinstance(schema, dict):
        raise PromptParseError("## Schema 必須是 JSON object")
    try:
        from jsonschema import Draft202012Validator

        Draft202012Validator.check_schema(schema)
    except Exception as e:  # noqa: BLE001  含 jsonschema.SchemaError 與未安裝情況
        raise PromptParseError(f"## Schema 非合法 JSON Schema：{e}") from e

    # 占位符必須存在（避免送出未填充模板）
    for ph in (POLARITY_PLACEHOLDER, TEXT_PLACEHOLDER):
        if ph not in user_template:
            raise PromptParseError(f"## User 段缺少占位符 {ph}")

    sha = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return ParsedPrompt(
        system=system,
        user_template=user_template,
        schema=schema,
        raw=raw,
        sha256=sha,
        version=version or (Path(path).stem if path else sha[:12]),
        path=path,
    )


def parse_prompt_file(path: str | Path, *, version: str = "") -> ParsedPrompt:
    """讀檔並解析（sha256 以檔案 UTF-8 位元組計）。"""
    p = Path(path)
    raw = p.read_text(encoding="utf-8")
    return parse_prompt_text(raw, version=version, path=str(p))


def extract_sections(raw: str, names: list[str]) -> dict[str, str]:
    """通用：抽出指定 level-2 section 各自第一個 fenced block（缺任一即失敗）。

    Generator/Auditor prompt 用（其 schema 由程式提供，故不含 ## Schema 段）。
    """
    lines = raw.splitlines()
    headers: dict[str, int] = {}
    for idx, line in enumerate(lines):
        m = _HEADER_RE.match(line.strip())
        if m:
            headers.setdefault(m.group(1).capitalize(), idx)
    out: dict[str, str] = {}
    for name in names:
        key = name.capitalize()
        if key not in headers:
            raise PromptParseError(f"缺少必要段落 '## {name}'")
        out[name] = _first_fenced_block(lines, headers[key])
    return out


@dataclass(frozen=True)
class GenPrompt:
    """Generator/Auditor prompt：system + user 模板（user 含 {SPEC} 占位符）+ 溯源。"""

    system: str
    user_template: str
    sha256: str
    version: str

    def render_user(self, spec: str) -> str:
        """把 {SPEC} 填入 user 模板（str.replace；spec 內大括號視為字面量）。"""
        if "{SPEC}" not in self.user_template:
            raise PromptParseError("## User 段缺少占位符 {SPEC}")
        return self.user_template.replace("{SPEC}", spec)


def parse_gen_prompt_file(path: str | Path, *, version: str = "") -> GenPrompt:
    """解析 Generator/Auditor prompt（僅 ## System + ## User；{SPEC} 由 orchestrator 填）。"""
    p = Path(path)
    raw = p.read_text(encoding="utf-8")
    secs = extract_sections(raw, ["System", "User"])
    if "{SPEC}" not in secs["User"]:
        raise PromptParseError("## User 段缺少占位符 {SPEC}")
    sha = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return GenPrompt(
        system=secs["System"],
        user_template=secs["User"],
        sha256=sha,
        version=version or p.stem,
    )

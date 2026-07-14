"""類別定義文檔生成器（C1 交付物）——從 docs/prompts/*.md 生成人讀版類別定義文檔。

單一生成方向：**prompts → 文檔**，不反向（文檔僅供人讀，判準修改一律回 prompt md，不得直接改
生成物）。來源與 prompt_source（判決引擎同一份 SSOT）一致：域機器值←檔名尾綴、L2 面向←
facet_catalog「■ CODE LABEL：定義」解析、域中文名/action/owner←config/ai_judge/domains.json；
本域界線（✅屬本域／❌常見誤判／⛔明確禁止）另從各域 prompt 的 <domain_boundary> 段解析，供人
快速查「這則問題該歸哪域」而不必讀完整支 prompt md。

用法（scripts/ 未掛載，先 docker cp；純讀 prompt_source + domains.json，不需 DB 連線）：
    docker cp scripts/tools/gen_taxonomy_doc.py kkday-ai-quality-backend:/app/scripts/tools/
    docker exec kkday-ai-quality-backend python /app/scripts/tools/gen_taxonomy_doc.py \
        --out /app/docs/類別定義_V0.1.md
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "backend"))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from app.judge import prompt_source  # noqa: E402

_BOUNDARY_RE = re.compile(r"<domain_boundary>(.*?)</domain_boundary>", re.S)
_FACET_LINE_RE = re.compile(r"^■\s*(C-\d+-\d+)\s+([^：\n]+)：(.*)$", re.M)


def _parse_boundary(system: str) -> dict[str, str | list[str]]:
    """解析 `<domain_boundary>` 段 → {header, allow:[...], forbid:[...], deny:[...]}。

    格式固定（7 支域 prompt 一致）：header 行（`【域名（C-N）】...`）→ `　✅屬本域：` 條列 →
    `　❌禁歸本域（常見誤判）：` 條列 → `　⛔不得：` 條列（每條 `　・` 開頭）。

    Args:
        system: 域 prompt 的 System 全文。

    Returns:
        缺 `<domain_boundary>` 段（理論上不會，7 支皆有）時回空 dict。
    """
    m = _BOUNDARY_RE.search(system)
    if not m:
        return {}
    block = m.group(1)
    lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
    header = next((ln for ln in lines if ln.startswith("【")), "")

    def _section(start_marker: str, end_markers: tuple[str, ...]) -> list[str]:
        out: list[str] = []
        collecting = False
        for ln in lines:
            if ln.startswith(start_marker):
                collecting = True
                continue
            if collecting and any(ln.startswith(m2) for m2 in end_markers):
                break
            if collecting and ln.startswith("・"):
                out.append(ln.removeprefix("・"))
        return out

    return {
        "header": header,
        "allow": _section("✅屬本域", ("❌", "⛔")),
        "forbid": _section("❌禁歸本域", ("⛔",)),
        "deny": _section("⛔不得", ("If",)),
    }


def _parse_facet_defs(system: str) -> dict[str, str]:
    """解析 `<facet_catalog>` 段每行「■ CODE LABEL：定義」→ {code: 定義全文}（供比對正例/反例用完整
    一句話定義，比 `prompt_source._parse_facets` 只取 label 更完整）。
    """
    return {code: definition.strip() for code, _label, definition in _FACET_LINE_RE.findall(system)}


def _domain_section(pid: str) -> str:
    """單一域的完整 markdown 區塊：定義 + 邊界（✅❌⛔）+ L2 面向表。"""
    p = prompt_source.load(pid)
    domain = prompt_source._domain_of(pid)
    dm = prompt_source._domain_meta(domain)
    facets = prompt_source._parse_facets(p["system"])
    defs = _parse_facet_defs(p["system"])
    b = _parse_boundary(p["system"])
    rule_code = prompt_source.rule_code_for_prompt(pid)

    lines = [
        f"## {rule_code.replace('prompt_', '')} {dm.get('label', domain)}（{domain}）",
        "",
        f"> 建議動作：`{dm.get('action', '')}`"
        + (f"　·　負責單位：{dm.get('owner')}" if dm.get("owner") else ""),
        "",
    ]
    if b.get("header"):
        lines += [f"**定義**：{b['header']}", ""]
    if b.get("allow"):
        lines += ["**✅ 屬本域**", ""] + [f"- {x}" for x in b["allow"]] + [""]
    if b.get("forbid"):
        lines += ["**❌ 常見誤判**", ""] + [f"- {x}" for x in b["forbid"]] + [""]
    if b.get("deny"):
        lines += ["**⛔ 明確禁止**", ""] + [f"- {x}" for x in b["deny"]] + [""]

    lines += ["**L2 面向**", "", "| code | 面向 | 定義 |", "|---|---|---|"]
    for f in facets:
        code = f["code"]
        defn = defs.get(code, f["label"]).replace("|", "\\|")
        lines.append(f"| {code} | {f['label']} | {defn} |")
    lines.append("")
    return "\n".join(lines)


def generate() -> str:
    """組完整文檔：頁首（源＋六域速覽）+ 六域各自區塊。"""
    struct = prompt_source.structure()
    overview = ["| 域 | 中文名 | L2 面向數 |", "|---|---|---|"]
    # structure()/DOMAIN_PROMPT_IDS 皆按 01_C-1..06_C-6 固定順序遍歷，兩者逐一對應（同一份 SSOT）。
    for pid, d in zip(prompt_source.DOMAIN_PROMPT_IDS, struct["domains"], strict=True):
        rule = prompt_source.rule_code_for_prompt(pid).replace("prompt_", "")
        overview.append(f"| {rule} | {d['domain_label']}（{d['domain']}） | {len(d['facets'])} |")

    parts = [
        "# 類別定義 V0.1",
        "",
        "> 本文檔由 `scripts/tools/gen_taxonomy_doc.py` 從 `docs/prompts/*.md`"
        "（Prompt-as-Source 判準唯一真相源）自動生成，**單向生成，不得手改**——"
        "調整判準請改對應域 prompt md 後重新產生本檔。",
        "",
        "## 六域速覽",
        "",
        *overview,
        "",
    ]
    for pid in prompt_source.DOMAIN_PROMPT_IDS:
        parts.append(_domain_section(pid))
    return "\n".join(parts)


def main() -> None:
    ap = argparse.ArgumentParser(description="生成類別定義 V0.1 文檔（C1 交付物）")
    ap.add_argument(
        "--out", default="docs/類別定義_V0.1.md", help="輸出路徑（預設 repo 根 docs/ 下）"
    )
    args = ap.parse_args()
    doc = generate()
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(doc, encoding="utf-8")
    print(f"→ {out_path}（{len(doc)} 字元）")


if __name__ == "__main__":
    main()

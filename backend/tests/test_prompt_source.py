"""Prompt-as-Source 載入層測試：parse（含 ## Meta）/ load（DB-first→檔案 fallback）/ validate / seed。

自洽 drift 護欄（樹退役後 prompt 內部同源）在此鎖死：
- 存檔驗證：域 prompt 的 Schema l2_code enum == Meta.facets codes（validate 單元測）。
- structure()：6 支域 prompt 的 ## Meta 彙整供 ai_judge 建索引（取代 DB 樹）。

測試庫（temp_db）為空 → prompt_source 回退讀 docs/prompts md（git 版控現行，含 Meta），
故測試確定性、不依賴外部 DB 狀態。
"""

from __future__ import annotations

import copy

import pytest

from app.core import db
from app.judge import prompt_source as ps

# ─────────────────────────── 純解析（無 DB）───────────────────────────
_SAMPLE_MD = """# 範例判官

## System

```
<judge>
你是測試判官。輸出 JSON。
</judge>
```

## User

```
整體傾向：{POLARITY}
<review_text>
{TEXT}
</review_text>
```

## Schema

```json
{
  "type": "object",
  "required": ["attributions"],
  "properties": {
    "attributions": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "l2_code": {"type": "string", "enum": ["C-3-1", "C-3-2"]}
        }
      }
    }
  }
}
```

## Meta

```json
{
  "domain": "supplier",
  "domain_label": "供應商履約",
  "action": "penalize_breach",
  "owner": "",
  "facets": [{"code": "C-3-1", "label": "人員服務"}, {"code": "C-3-2", "label": "駕駛接送"}]
}
```
"""


def test_parse_md_extracts_sections_and_meta():
    """parse_md 正確抽出 title/system/user_template/schema + 選填 ## Meta。"""
    p = ps.parse_md(_SAMPLE_MD)
    assert p["title"] == "範例判官"
    assert "<judge>" in p["system"] and "輸出 JSON" in p["system"]
    assert "{POLARITY}" in p["user_template"] and "{TEXT}" in p["user_template"]
    assert p["schema"]["properties"]["attributions"]["items"]["properties"]["l2_code"]["enum"] == [
        "C-3-1",
        "C-3-2",
    ]
    assert p["meta"]["domain"] == "supplier"
    assert {f["code"] for f in p["meta"]["facets"]} == {"C-3-1", "C-3-2"}


def test_parse_md_missing_section_raises():
    """缺 `## Schema` 節 → ValueError（fail-loud）。"""
    bad = _SAMPLE_MD.split("## Schema")[0]
    with pytest.raises(ValueError, match="Schema"):
        ps.parse_md(bad)


def test_parse_md_missing_title_raises():
    """缺 H1 標題 → ValueError。"""
    bad = _SAMPLE_MD.split("\n", 1)[1]  # 去掉首行 H1
    with pytest.raises(ValueError, match="H1"):
        ps.parse_md(bad)


def test_parse_md_bad_schema_json_raises():
    """Schema 圍欄內非合法 JSON → ValueError。"""
    bad = _SAMPLE_MD.replace('"type": "object",\n  "required"', '"type": "object" "required"')
    with pytest.raises(ValueError, match="Schema JSON"):
        ps.parse_md(bad)


# ─────────────────────────── 載入 / 驗證（temp_db；空庫→檔案 fallback）───────────────────────────
def test_load_all_prompts_from_file(temp_db):
    """空庫下 load 每支 prompt 皆回退 docs md 並解析出四節。"""
    ps.reload()
    for pid in ps.PROMPT_IDS:
        p = ps.load(pid)
        assert p["title"] and p["system"] and p["user_template"]
        assert isinstance(p["schema"], dict)


def test_validate_all_committed_prompts_pass(temp_db):
    """git 版控的 7 支 md 全部通過 validate（含自洽 drift 護欄:域 prompt enum==Meta.facets）。"""
    from app.core.paths import DOCS_PROMPTS_DIR

    for pid in ps.PROMPT_IDS:
        text = (DOCS_PROMPTS_DIR / f"{pid}.md").read_text(encoding="utf-8")
        ps.validate(text, pid)  # 不拋即通過


def test_validate_missing_text_placeholder_raises(temp_db):
    """User 模板缺 {TEXT} → ValueError。"""
    bad = _SAMPLE_MD.replace("{TEXT}", "評論內容")
    with pytest.raises(ValueError, match="TEXT"):
        ps.validate(bad, "03_C-3_supplier")


def test_validate_domain_missing_polarity_raises(temp_db):
    """域 prompt 的 User 模板缺 {POLARITY} → ValueError。"""
    bad = _SAMPLE_MD.replace("整體傾向：{POLARITY}\n", "")
    with pytest.raises(ValueError, match="POLARITY"):
        ps.validate(bad, "03_C-3_supplier")


def test_validate_enum_meta_mismatch_raises(temp_db):
    """域 prompt Schema enum 與 Meta.facets 不一致（enum 有 C-3-99、Meta 無）→ 自洽護欄拒（ValueError）。"""
    bad = _SAMPLE_MD.replace('["C-3-1", "C-3-2"]', '["C-3-1", "C-3-99"]')
    with pytest.raises(ValueError, match="不一致"):
        ps.validate(bad, "03_C-3_supplier")


def test_validate_domain_missing_meta_raises(temp_db):
    """域 prompt 缺 ## Meta 結構節 → ValueError。"""
    bad = _SAMPLE_MD.split("## Meta")[0].rstrip()
    with pytest.raises(ValueError, match="Meta"):
        ps.validate(bad, "03_C-3_supplier")


# ─────────────────────────── 自洽 drift 護欄 ───────────────────────────
def test_domain_schema_enum_equals_meta_facets(temp_db):
    """每支域 prompt 的 Schema l2_code enum == Meta.facets codes（判準 schema 與結構註冊表同源自洽）。"""
    ps.reload()
    facets_by_domain = {
        d["domain"]: {f["code"] for f in d["facets"]} for d in ps.structure()["domains"]
    }
    for pid in ps.DOMAIN_PROMPT_IDS:
        enum = ps.schema_l2_enum_for(pid)
        meta = ps.load(pid)["meta"] or {}
        facet_codes = {f["code"] for f in (meta.get("facets") or [])}
        assert enum == facet_codes, f"{pid}: enum={sorted(enum)} facets={sorted(facet_codes)}"
        assert facet_codes == facets_by_domain.get(meta.get("domain"), set())


# ─────────────────────────── DB 版本化 / seed ───────────────────────────
def test_default_prompt_content_wraps_md(temp_db):
    """default_prompt_content 讀 md 包成 {_meta, text} 版本化格式。"""
    c = db.default_rule_content("prompt_C-3")
    assert c["_meta"]["kind"] == "prompt"
    assert c["_meta"]["label"]
    assert "## System" in c["text"] and "## Schema" in c["text"]


def test_seed_and_load_prefers_db(temp_db):
    """seed 後 prompt_* 有 active 版；改 DB active 後 load 取回 DB 版（非檔案）。"""
    res = db.seed_rules_from_files()
    for code in ps.PROMPT_RULE_CODES:
        assert res.get(code) == "seeded"
        assert db.get_rule_active(code) is not None
    # 改 DB active 的 text（在 title 後插一行註記），reload 後 load 應取回 DB 版
    content = copy.deepcopy(db.get_rule_active("prompt_polarity"))
    content["text"] = content["text"].replace("# ", "# [DB版] ", 1)
    db.save_rule_version("prompt_polarity", content, note="test", author="pytest")
    ps.reload()
    assert ps.load("00_polarity")["title"].startswith("[DB版]")


def test_bulk_reset_excludes_prompts(temp_db):
    """reset_all_rule_defaults（歸因分類 bulk）不掃 prompt_*（各有獨立恢復入口）。"""
    res = db.reset_all_rule_defaults(author="pytest")
    reset_codes = {r["rule_code"] for r in res["reset"]}
    assert not (reset_codes & set(ps.PROMPT_RULE_CODES))

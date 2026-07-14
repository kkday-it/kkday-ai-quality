"""Prompt-as-Source 載入層測試：parse / load（DB-first→檔案 fallback）/ validate / seed / 動態分類。

分類的類別＋層級＋域 metadata 唯一源＝各域 prompt 的 `## Taxonomy` 節（```json）；程式碼零 taxonomy 假設：
- structure()：6 支域 prompt 的 `## Taxonomy` 派生分類樹 + 域 meta（含 evidence_gated）供 ai_judge 建索引。
- Schema l2_code enum 由 `## Taxonomy` 派生注入（prompt 不手寫 code 清單、零 drift）。

測試庫（temp_db）為空 → prompt_source 回退讀 prompts md（git 版控現行），故測試確定性、不依賴 DB。
"""

from __future__ import annotations

import copy

import pytest

from app.core import db
from app.judge import prompt_source as ps

# ─────────────────────────── 純解析（無 DB）───────────────────────────
# 域 prompt 四節：System（模型面向）/ User / Taxonomy（機器面向，分類唯一源）/ Schema。
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

## Taxonomy

```json
{"code":"supplier","label":"供應商履約","action":"penalize_breach","owner":"","evidence_gated":true,
 "children":[
  {"code":"C-3-1","label":"人員服務"},
  {"code":"C-3-2","label":"駕駛接送"}
 ]}
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
          "l2_code": {"type": "string"}
        }
      }
    }
  }
}
```
"""


def test_parse_md_extracts_sections():
    """parse_md 抽 title/system/user_template/schema + taxonomy（enum 由 load 派生，parse 不含）。"""
    p = ps.parse_md(_SAMPLE_MD)
    assert p["title"] == "範例判官"
    assert (
        "<judge>" in p["system"] and "taxonomy" not in p["system"].lower()
    )  # taxonomy 已移出 System
    assert p["taxonomy"]["code"] == "supplier"  # 獨立 ## Taxonomy 節派生
    assert "{POLARITY}" in p["user_template"] and "{TEXT}" in p["user_template"]
    l2 = p["schema"]["properties"]["attributions"]["items"]["properties"]["l2_code"]
    assert l2 == {"type": "string"}  # parse_md 不注入 enum（load 才派生）


def test_taxonomy_from_parse_md_and_flatten():
    """parse_md 抽出 `## Taxonomy` → root（域節點+meta）+ 攤平 facet 清單。"""
    p = ps.parse_md(_SAMPLE_MD)
    root = p["taxonomy"]
    assert root["code"] == "supplier" and root["evidence_gated"] is True
    facets = ps._flatten_taxonomy(root)
    assert [f["code"] for f in facets] == ["C-3-1", "C-3-2"]
    assert facets[0]["label"] == "人員服務"


def test_flatten_taxonomy_variable_depth():
    """可變深度：巢狀 children 全部攤平（層數純由 prompt 的 `## Taxonomy` 定義）。"""
    root = {
        "code": "supplier",
        "children": [
            {
                "code": "C-3-1",
                "label": "人員服務",
                "children": [{"code": "C-3-1-1", "label": "導遊素質"}],
            },
            {"code": "C-3-2", "label": "駕駛接送"},
        ],
    }
    assert [f["code"] for f in ps._flatten_taxonomy(root)] == ["C-3-1", "C-3-1-1", "C-3-2"]


def test_parse_taxonomy_section_bad_json_raises():
    """`## Taxonomy` 圍欄內非合法 JSON → ValueError；無 `## Taxonomy` 節 → None（polarity 場景）。"""
    bad = "## Taxonomy\n\n```json\n{不是 json}\n```\n"
    with pytest.raises(ValueError, match="Taxonomy"):
        ps._parse_taxonomy_section(bad)
    assert ps._parse_taxonomy_section("## System\n\n```\nx\n```\n") is None  # 無 Taxonomy 節 → None


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
    """空庫下 load 每支 prompt 皆回退 md 並解析出各節。"""
    ps.reload()
    for pid in ps.PROMPT_IDS:
        p = ps.load(pid)
        assert p["title"] and p["system"] and p["user_template"]
        assert isinstance(p["schema"], dict)


def test_load_derives_enum_from_taxonomy(temp_db):
    """域 prompt load 後 Schema l2_code enum＝`## Taxonomy` 派生（prompt 不手寫）。"""
    ps.reload()
    for pid in ps.DOMAIN_PROMPT_IDS:
        root = ps.load(pid)["taxonomy"]
        taxo_codes = {f["code"] for f in ps._flatten_taxonomy(root)}
        assert ps.schema_l2_enum_for(pid) == taxo_codes, pid


def test_validate_all_committed_prompts_pass(temp_db):
    """git 版控的 7 支 md 全部通過 validate（域 prompt `## Taxonomy` 可解析、至少一 facet）。"""
    from app.core.paths import PROMPTS_DIR

    for pid in ps.PROMPT_IDS:
        text = (PROMPTS_DIR / f"{pid}.md").read_text(encoding="utf-8")
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


def test_validate_domain_missing_taxonomy_raises(temp_db):
    """域 prompt 缺 `## Taxonomy` 節 → ValueError（fail-loud，無分類源）。"""
    bad = _SAMPLE_MD.replace("## Taxonomy", "## NotTaxonomy")  # 改名 → 找不到 Taxonomy 節
    with pytest.raises(ValueError, match="Taxonomy"):
        ps.validate(bad, "03_C-3_supplier")


# ─────────────────────────── 結構派生（`## Taxonomy` 唯一源）───────────────────────────
def test_structure_from_taxonomy(temp_db):
    """structure() 域機器值＝檔名尾綴、label/action/evidence_gated 來自各域 `## Taxonomy` root。"""
    ps.reload()
    doms = {d["domain"]: d for d in ps.structure()["domains"]}
    assert set(doms) == {"content", "quality", "supplier", "platform", "service", "customer"}
    assert doms["platform"]["domain_label"] == "平台與系統"
    assert doms["quality"]["domain_label"] == "商品品質"
    assert doms["content"]["action"] == "clarify_wording"
    assert doms["supplier"]["evidence_gated"] is True  # 證據閘自 taxonomy root
    assert doms["content"]["evidence_gated"] is False
    assert {f["code"] for f in doms["content"]["facets"]} == {f"C-1-{i}" for i in range(1, 8)}


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

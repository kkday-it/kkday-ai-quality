"""Markdown 解析與占位符 + fail-loud（PRD §10.1/§19）。"""

from __future__ import annotations

import prompt_parser as pp
import pytest

_MIN = (
    "## System\n```\nSYS\n```\n"
    "## User\n```\n整體傾向：{POLARITY}\n<review_text>\n{TEXT}\n</review_text>\n```\n"
    '## Schema\n```json\n{"type":"object","additionalProperties":false,'
    '"required":["attributions"],"properties":{"attributions":{"type":"array"}}}\n```\n'
)


def test_parse_real_c1_prompt(c1_prompt_path):
    p = pp.parse_prompt_file(c1_prompt_path)
    assert p.system and "{POLARITY}" not in p.system
    assert "{POLARITY}" in p.user_template and "{TEXT}" in p.user_template
    assert p.schema["type"] == "object"
    assert p.schema_l2_enum() == ["C-1-1", "C-1-2", "C-1-3", "C-1-4", "C-1-5", "C-1-6", "C-1-7"]
    assert len(p.sha256) == 64


def test_render_user_substitutes_and_no_leftover():
    p = pp.parse_prompt_text(_MIN)
    out = p.render_user("negative", "頁面沒寫清楚")
    assert "negative" in out and "頁面沒寫清楚" in out
    assert "{POLARITY}" not in out and "{TEXT}" not in out


def test_render_user_text_with_braces_is_literal():
    p = pp.parse_prompt_text(_MIN)
    out = p.render_user("neutral", "客訴含 {奇怪} 大括號與 {TEXT} 字面")
    assert "{奇怪}" in out  # 不被 format 破壞


def test_missing_section_fails():
    bad = "## System\n```\nX\n```\n## User\n```\n{POLARITY}{TEXT}\n```\n"  # 缺 Schema
    with pytest.raises(pp.PromptParseError):
        pp.parse_prompt_text(bad)


def test_missing_placeholder_fails():
    bad = _MIN.replace("{TEXT}", "評論")
    with pytest.raises(pp.PromptParseError):
        pp.parse_prompt_text(bad)


def test_invalid_json_schema_fails():
    bad = _MIN.replace('{"type":"object"', "{bad json")
    with pytest.raises(pp.PromptParseError):
        pp.parse_prompt_text(bad)


def test_no_fenced_block_fails():
    bad = '## System\n\n## User\n```\n{POLARITY}{TEXT}\n```\n## Schema\n```json\n{"type":"object"}\n```\n'
    with pytest.raises(pp.PromptParseError):
        pp.parse_prompt_text(bad)


def test_gen_prompt_parse(prompts_dir):
    g = pp.parse_gen_prompt_file(prompts_dir / "generators" / "c1_generator.md")
    a = pp.parse_gen_prompt_file(prompts_dir / "generators" / "c1_auditor.md")
    assert "{SPEC}" in g.user_template and "{SPEC}" in a.user_template
    assert "SPEC-X" in g.render_user("SPEC-X")


def test_extract_sections_missing_raises():
    with pytest.raises(pp.PromptParseError):
        pp.extract_sections("## System\n```\nx\n```\n", ["System", "User"])

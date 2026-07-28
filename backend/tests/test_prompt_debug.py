"""售後根因 Prompt 調試台：分類 SSOT、單一輸出契約、Prompt 版本庫與單次配置覆蓋。"""

from __future__ import annotations

import json
from types import SimpleNamespace

from app.judge import prompt_debug
from app.judge import prompt_debug_versions as versions


def _base_result(**overrides):
    """一筆合法的非 OOT 判定（單一契約：全欄禁 null、keywords 陣列、urgency 1–5）。"""
    value = {
        "category": "憑證/取票資訊未送達或不知如何使用",
        "theme": "[104] 訂單確認問題",
        "likely_cause": "憑證送達延遲",
        "modify_target": "n/a",
        "summary": "旅客出發前仍未收到電子票，要求協助確認送達時程。",
        "keywords": ["電子票", "未收到", "出發前"],
        "sentiment": "negative",
        "urgency": 4,
        "money_mention_flag": False,
        "fulfillment_mention_flag": True,
        "multi_issue_flag": False,
        "no_actionable_content": False,
        "confidence": 0.93,
    }
    value.update(overrides)
    return value


def test_defaults_carry_latest_prompt_and_taxonomy_derived_schema() -> None:
    """payload 只有一套契約：最新版 Prompt ＋ 由分類 SSOT 派生的 schema/欄位卡。"""
    payload = prompt_debug.defaults_payload()
    assert payload["category_count"] == 25
    assert payload["theme_count"] == 5
    # 版本名即檔名時間戳，且必須真的是版本庫最新版
    assert payload["prompt_version"] == versions.latest_version()
    assert payload["prompt_versions"][0] == payload["prompt_version"]
    assert payload["system_prompt"] == versions.latest_prompt()
    # 靜態快照：分類庫已內嵌，不該再留模板佔位符
    assert "{{TAXONOMY_JSON}}" not in payload["system_prompt"]

    schema = payload["output_schema"]
    assert "__OUT_OF_TAXONOMY__" in schema["properties"]["category"]["enum"]
    assert "n/a" in schema["properties"]["modify_target"]["enum"]
    assert "$schema" not in schema
    assert [field["key"] for field in payload["output_fields"]] == [
        "theme",
        "category",
        "likely_cause",
        "modify_target",
        "summary",
        "keywords",
        "sentiment",
        "urgency",
        "money_mention_flag",
        "fulfillment_mention_flag",
        "multi_issue_flag",
        "no_actionable_content",
        "confidence",
    ]
    # 已清退的欄位不得復活（tail_theme / urgency_flag＝v2 契約；oot_subtype＝2026-07-28 全棧退役）
    assert {"tail_theme", "urgency_flag", "oot_subtype"}.isdisjoint(
        {field["key"] for field in payload["output_fields"]}
    )
    assert payload["sources"]["field_definitions_document"]["document_id"] == (
        "1FFFqsGPUhOd0oVG4uDbSgVfsdqdYYRuy5fLIE0tYpMA"
    )


def test_output_cascade_narrows_each_level_to_its_parent_branch() -> None:
    """L1→L2→L3 級聯由 SSOT 派生：下層清單必須是上層那一支底下的值，且與 schema enum 同源。"""
    taxonomy = prompt_debug.load_taxonomy()
    cascade = prompt_debug.output_cascade(taxonomy)
    schema = prompt_debug.output_schema(taxonomy)

    assert cascade["category"]["parent"] == "theme"
    assert cascade["likely_cause"]["parent"] == "category"

    by_theme = cascade["category"]["options_by_parent"]
    # 5 個主題 + OOT 分支；攤平後恰為 schema 的 category enum（不多不少，證明沒有漏掛的類）
    assert len(by_theme) == 6
    assert sorted(c for opts in by_theme.values() for c in opts) == sorted(
        schema["properties"]["category"]["enum"]
    )
    assert by_theme["其他"] == ["__OUT_OF_TAXONOMY__"]

    # 每個 category 都掛在自己 theme 底下（抽一類驗證，避免只是形狀對但歸屬錯）
    assert "取消政策揭露不清" in by_theme["[101] 訂單取消"]
    assert "取消政策揭露不清" not in by_theme["[93] 訂單申請修改"]

    by_category = cascade["likely_cause"]["options_by_parent"]
    assert by_category["__OUT_OF_TAXONOMY__"] == ["n/a"]
    for row in taxonomy["categories"]:
        assert by_category[row["name"]] == row["likely_causes"]


def test_defaults_payload_carries_cascade_for_review_controls() -> None:
    """人工評判的下拉靠 payload 的 output_cascade 收窄，缺了它 L2 會退回攤平的 25 類。"""
    payload = prompt_debug.defaults_payload()
    assert payload["output_cascade"]["category"]["parent"] == "theme"
    assert payload["output_cascade"]["likely_cause"]["parent"] == "category"


def test_slashes_inside_controlled_causes_are_not_split() -> None:
    taxonomy = prompt_debug.load_taxonomy()
    causes = {cause for category in taxonomy["categories"] for cause in category["likely_causes"]}
    assert "下單流程統編/抬頭欄位易漏填或誤填" in causes
    assert "代收轉付收據性質未於下單/商品頁說明" in causes
    assert "用戶對發票/收據/三聯式概念混淆" in causes
    assert "商品頁說明" not in causes


def test_validate_result_accepts_controlled_non_oot() -> None:
    assert prompt_debug.validate_result(_base_result()) == []


def test_validate_result_enforces_summary_length_from_field_definition() -> None:
    issues = prompt_debug.validate_result(_base_result(summary="太短"))
    assert issues and issues[0].startswith("Schema summary:")


def test_validate_result_rejects_cross_category_cause_and_theme() -> None:
    issues = prompt_debug.validate_result(
        _base_result(theme="[101] 訂單取消", likely_cause="退款作業時程長")
    )
    assert "theme 必須是 [104] 訂單確認問題" in issues
    assert "likely_cause 不屬於該 category 的受控選項" in issues


def test_validate_result_accepts_oot_contract() -> None:
    value = _base_result(
        category="__OUT_OF_TAXONOMY__",
        theme="其他",
        likely_cause="n/a",
    )
    assert prompt_debug.validate_result(value) == []


def test_validate_result_enforces_no_actionable_content_linkage() -> None:
    """no_actionable_content=true 必須連動 OOT ＋ keywords 清空。"""
    assert "no_actionable_content=true 時 category 必須是 __OUT_OF_TAXONOMY__" in (
        prompt_debug.validate_result(_base_result(no_actionable_content=True))
    )
    value = _base_result(
        category="__OUT_OF_TAXONOMY__",
        theme="其他",
        likely_cause="n/a",
        no_actionable_content=True,
        keywords=[],
    )
    assert prompt_debug.validate_result(value) == []


def test_validate_result_requires_modify_target_for_93() -> None:
    value = _base_result(
        category="修改受限（商品規則/供應商政策不允許改）",
        theme="[93] 訂單申請修改",
        likely_cause="商品規則不允許改",
    )
    assert "[93] category 必須填 modify_target（不可為 n/a）" in prompt_debug.validate_result(value)
    value["modify_target"] = "改日期/時段/班次"
    assert prompt_debug.validate_result(value) == []


# ── Prompt 版本庫（時間戳一版一檔，永遠取最新）──────────────────────────────────


def test_repo_prompt_dir_has_only_timestamp_versions() -> None:
    """repo 內的版本目錄必須解得出最新版（防有人改回 vN.md 命名而靜默失效）。"""
    assert versions.list_versions(), "版本庫是空的：檔名需為 YYYY-MM-DD-HHMMSS.md"
    assert versions.latest_prompt().strip()


def test_latest_is_newest_filename_not_mtime(monkeypatch, tmp_path) -> None:
    """最新＝檔名時間戳最大者；先寫的舊名檔即使 mtime 較新也不該勝出。"""
    monkeypatch.setattr(versions, "PROMPT_DIR", tmp_path)
    (tmp_path / "2026-07-27-185628.md").write_text("新版", encoding="utf-8")
    (tmp_path / "2026-01-01-090000.md").write_text("舊版", encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text("不是版本檔", encoding="utf-8")

    assert versions.list_versions() == ["2026-07-27-185628", "2026-01-01-090000"]
    assert versions.latest_prompt() == "新版"


def test_save_creates_new_version_and_skips_identical(monkeypatch, tmp_path) -> None:
    """存檔即成為最新版；與最新版逐字相同則不建檔。"""
    monkeypatch.setattr(versions, "PROMPT_DIR", tmp_path)
    (tmp_path / "2026-01-01-090000.md").write_text("舊版\n", encoding="utf-8")

    created = versions.save("改過的 Prompt")
    assert created["created"] is True
    assert created["version"] > "2026-01-01-090000"
    assert versions.latest_prompt().strip() == "改過的 Prompt"

    again = versions.save("改過的 Prompt")
    assert again == {"version": created["version"], "created": False}
    assert len(versions.list_versions()) == 2


def test_resolve_falls_back_to_latest_and_flags_edits(monkeypatch, tmp_path) -> None:
    """空字串＝取最新版並標版本名；改過的內容版本名留空（只能靠 sha256 追）。"""
    monkeypatch.setattr(versions, "PROMPT_DIR", tmp_path)
    (tmp_path / "2026-01-01-090000.md").write_text("線上版\n", encoding="utf-8")

    assert versions.resolve("  ") == ("線上版\n", "2026-01-01-090000")
    assert versions.resolve("線上版") == ("線上版", "2026-01-01-090000")
    assert versions.resolve("臨時改一句") == ("臨時改一句", "")


def test_read_version_rejects_path_traversal(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(versions, "PROMPT_DIR", tmp_path)
    for bad in ("../../etc/passwd", "v3", "2026-07-27"):
        try:
            versions.read_version(bad)
        except ValueError:
            continue
        raise AssertionError(f"應拒絕非法版本名：{bad!r}")


def test_stream_frames_uses_final_chunk_usage_for_same_call(monkeypatch) -> None:
    raw = json.dumps(_base_result(), ensure_ascii=False)
    usage = SimpleNamespace(
        prompt_tokens=1_000,
        completion_tokens=200,
        prompt_tokens_details=SimpleNamespace(cached_tokens=100),
        completion_tokens_details=SimpleNamespace(reasoning_tokens=40),
    )
    chunks = iter(
        [
            SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content=raw[:40]))], usage=None
            ),
            SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content=raw[40:]))], usage=None
            ),
            SimpleNamespace(choices=[], usage=usage),
        ]
    )
    monkeypatch.setattr(prompt_debug.app_settings, "resolve_provider_token", lambda _: "sk-test")
    monkeypatch.setattr(prompt_debug, "_request_compat", lambda cfg, kwargs: (chunks, []))
    recorded: list[dict] = []
    monkeypatch.setattr(
        prompt_debug,
        "_record_usage_best_effort",
        lambda cfg, payload, job_id: recorded.append(payload),
    )

    frames = list(
        prompt_debug.stream_frames(
            "[USER] 尚未收到電子票",
            "只輸出 JSON",
            {
                "token": "sk-test",
                "base_url": "",
                "model": "gpt-5-mini",
                "temperature": None,
                "thinking": "off",
                "reasoning_effort": "minimal",
            },
        )
    )

    assert sum(frame.startswith("event: delta") for frame in frames) == 2
    result_frame = next(frame for frame in frames if frame.startswith("event: result"))
    assert '"valid": true' in result_frame
    usage_frame = next(frame for frame in frames if frame.startswith("event: usage"))
    assert '"prompt_tokens": 1000' in usage_frame
    assert '"completion_tokens": 200' in usage_frame
    assert '"reasoning_tokens": 40' in usage_frame
    assert recorded and recorded[0]["total_tokens"] == 1_200
    assert recorded[0]["cost_usd"] > 0

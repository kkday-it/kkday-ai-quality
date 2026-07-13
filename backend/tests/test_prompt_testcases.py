"""B3 邊界測試集（prompt_testcases）：驗證 / CSV 解析 / CRUD / mock 抽樣。"""

from __future__ import annotations

import pytest

from app.core import db
from app.judge import prompt_eval
from app.judge import prompt_testcases as pt


# ─────────────────────────── 驗證（純函式，不需 DB）───────────────────────────
def test_validate_row_ok_normalizes() -> None:
    """合法 row：正規化空白 + tags 逗號字串轉 list。"""
    row = pt.validate_row(
        {
            "text": "  訂單一直沒出貨  ",
            "gold_l1": "supplier",
            "gold_l2": "C-3-1",
            "expected_polarity": "negative",
            "note": " 邊界案例 ",
            "tags": "a, b ,c",
        }
    )
    assert row["text"] == "訂單一直沒出貨"
    assert row["gold_l1"] == "supplier"
    assert row["gold_l2"] == "C-3-1"
    assert row["expected_polarity"] == "negative"
    assert row["note"] == "邊界案例"
    assert row["tags"] == ["a", "b", "c"]


def test_validate_row_allows_empty_gold_l2_and_optional_fields() -> None:
    """gold_l2/expected_polarity/note/tags 皆可留空——僅標「屬此域」不釘特定面向。"""
    row = pt.validate_row({"text": "這是供應商問題", "gold_l1": "supplier"})
    assert row["gold_l2"] == ""
    assert row["expected_polarity"] == ""
    assert row["tags"] == []


def test_validate_row_rejects_empty_text() -> None:
    with pytest.raises(ValueError, match="text 不可為空"):
        pt.validate_row({"text": "  ", "gold_l1": "supplier"})


def test_validate_row_rejects_unknown_gold_l1() -> None:
    with pytest.raises(ValueError, match="gold_l1 不合法"):
        pt.validate_row({"text": "x", "gold_l1": "not_a_domain"})


def test_validate_row_rejects_gold_l2_outside_domain() -> None:
    """gold_l2 須屬於 gold_l1 該域的 facet_catalog（跨域引用視為不合法）。"""
    with pytest.raises(ValueError, match="不屬於域"):
        pt.validate_row({"text": "x", "gold_l1": "supplier", "gold_l2": "C-1-1"})


def test_validate_row_rejects_bad_polarity() -> None:
    with pytest.raises(ValueError, match="expected_polarity 不合法"):
        pt.validate_row({"text": "x", "gold_l1": "supplier", "expected_polarity": "sad"})


# ─────────────────────────── CSV 解析 ───────────────────────────
def test_parse_csv_splits_valid_and_errors_with_row_numbers() -> None:
    csv_bytes = (
        "text,gold_l1,gold_l2,expected_polarity,note,tags\n"
        "訂單延誤,supplier,C-3-1,negative,,late\n"
        ",supplier,,,,\n"  # 第 3 行：text 空
        "描述不符,not_a_domain,,,,\n"  # 第 4 行：gold_l1 不合法
    ).encode()
    valid, errors = pt.parse_csv(csv_bytes)
    assert len(valid) == 1
    assert valid[0]["text"] == "訂單延誤"
    assert [e["row"] for e in errors] == [3, 4]
    assert "text 不可為空" in errors[0]["error"]
    assert "gold_l1 不合法" in errors[1]["error"]


def test_parse_csv_handles_utf8_bom() -> None:
    """Excel 存出的 CSV 常帶 BOM，utf-8-sig 解碼須正確剝除，不污染首欄欄名。"""
    csv_bytes = "﻿text,gold_l1\n有問題的文字,quality\n".encode()
    valid, errors = pt.parse_csv(csv_bytes)
    assert not errors
    assert valid[0]["gold_l1"] == "quality"


# ─────────────────────────── DB CRUD ───────────────────────────
def test_insert_list_update_delete_roundtrip(temp_db) -> None:
    tc_id = db.insert_prompt_testcase(
        {
            "text": "供應商延誤出貨",
            "gold_l1": "supplier",
            "gold_l2": "",
            "created_by": "qc@kkday.com",
        }
    )
    assert tc_id.startswith("tc_")

    got = db.get_prompt_testcase(tc_id)
    assert got is not None
    assert got["text"] == "供應商延誤出貨"
    assert got["enabled"] is True

    listed = db.list_prompt_testcases(gold_l1="supplier")
    assert listed["total"] == 1
    assert listed["items"][0]["id"] == tc_id

    assert db.update_prompt_testcase(tc_id, {"enabled": False}) is True
    assert db.get_prompt_testcase(tc_id)["enabled"] is False

    assert db.delete_prompt_testcase(tc_id) is True
    assert db.get_prompt_testcase(tc_id) is None
    assert db.delete_prompt_testcase(tc_id) is False


def test_bulk_insert_skips_duplicate_text(temp_db) -> None:
    """既有 text 與批次內互相重複者皆跳過，只計入 skipped，不報錯中斷。"""
    db.insert_prompt_testcase({"text": "重複案例", "gold_l1": "supplier"})
    rows = [
        {"text": "重複案例", "gold_l1": "supplier"},  # 與既有重複
        {"text": "新案例 A", "gold_l1": "quality"},
        {"text": "新案例 A", "gold_l1": "quality"},  # 批次內互相重複
    ]
    result = db.bulk_insert_prompt_testcases(rows)
    assert result == {"inserted": 1, "skipped": 2}
    assert db.list_prompt_testcases()["total"] == 2  # 既有 1 + 新增 1


def test_list_filters_by_tags_and_enabled(temp_db) -> None:
    db.insert_prompt_testcase({"text": "a", "gold_l1": "supplier", "tags": ["urgent"]})
    db.insert_prompt_testcase({"text": "b", "gold_l1": "supplier", "tags": ["minor"]})
    tc_c = db.insert_prompt_testcase({"text": "c", "gold_l1": "supplier", "tags": ["urgent"]})
    db.update_prompt_testcase(tc_c, {"enabled": False})

    urgent = db.list_prompt_testcases(tags=["urgent"])
    assert {r["text"] for r in urgent["items"]} == {"a", "c"}

    enabled_only = db.list_prompt_testcases(enabled=True)
    assert {r["text"] for r in enabled_only["items"]} == {"a", "b"}


def test_update_prompt_testcase_ignores_unknown_fields(temp_db) -> None:
    tc_id = db.insert_prompt_testcase({"text": "a", "gold_l1": "supplier"})
    assert db.update_prompt_testcase(tc_id, {"not_a_column": "x"}) is False


def test_enabled_testcases_excludes_disabled(temp_db) -> None:
    tc1 = db.insert_prompt_testcase({"text": "a", "gold_l1": "supplier"})
    db.insert_prompt_testcase({"text": "b", "gold_l1": "quality"})
    db.update_prompt_testcase(tc1, {"enabled": False})

    all_enabled = db.enabled_testcases()
    assert {r["text"] for r in all_enabled} == {"b"}

    scoped = db.enabled_testcases(gold_l1="quality")
    assert {r["text"] for r in scoped} == {"b"}


# ─────────────────────────── mock 抽樣（純資料轉換 + DB，無 LLM）───────────────────────────
def test_sample_domain_mock_splits_positive_and_abstain(temp_db) -> None:
    """gold_l1 同域者為正例（ref_l2s 非空）；他域者為本域棄權分母（ref_l2s 空）。"""
    db.insert_prompt_testcase({"text": "供應商延誤", "gold_l1": "supplier", "gold_l2": "C-3-1"})
    db.insert_prompt_testcase({"text": "商品描述不符", "gold_l1": "content"})

    samples = prompt_eval.sample_domain_mock("supplier")
    by_text = {s["text"]: s for s in samples}
    assert by_text["供應商延誤"]["ref_l2s"] == ["C-3-1"]
    assert by_text["供應商延誤"]["ref_primary"] == "C-3-1"
    assert by_text["商品描述不符"]["ref_l2s"] == []
    assert by_text["商品描述不符"]["ref_primary"] is None


def test_sample_domain_mock_empty_gold_l2_still_counts_as_hit(temp_db) -> None:
    """gold_l2 留空＝僅標「屬此域」：ref_l2s 仍需為 truthy（`[""]`），供 hit_rate 計入分母。"""
    db.insert_prompt_testcase({"text": "屬於此域但沒釘面向", "gold_l1": "supplier", "gold_l2": ""})
    samples = prompt_eval.sample_domain_mock("supplier")
    assert samples[0]["ref_l2s"] == [""]
    assert bool(samples[0]["ref_l2s"]) is True
    assert samples[0]["ref_primary"] is None


def test_sample_polarity_mock_only_takes_rows_with_expected_polarity(temp_db) -> None:
    db.insert_prompt_testcase(
        {"text": "有標傾向", "gold_l1": "supplier", "expected_polarity": "negative"}
    )
    db.insert_prompt_testcase({"text": "沒標傾向", "gold_l1": "supplier"})

    samples = prompt_eval.sample_polarity_mock()
    assert [s["text"] for s in samples] == ["有標傾向"]
    assert samples[0]["polarity"] == "negative"
    assert samples[0]["sentiment"] is None


def test_compute_polarity_metrics_null_safe_sentiment() -> None:
    """sentiment 為 None 之筆不計入 sentiment_match_rate 分母（B3 mock 無 sentiment 真值時不誤導）。"""
    records = [
        {
            "polarity": "negative",
            "sentiment": None,
            "pack_polarity": "negative",
            "pack_sentiment": 2,
        },
        {
            "polarity": "negative",
            "sentiment": None,
            "pack_polarity": "positive",
            "pack_sentiment": 4,
        },
    ]
    m = prompt_eval.compute_polarity_metrics(records)
    assert m["polarity_match_rate"] == 0.5
    assert m["sentiment_match_rate"] is None


# ─────────────────────────── run_eval(source="mock") 拒跑 stub ───────────────────────────
def test_run_eval_mock_rejects_stub_mode(monkeypatch) -> None:
    from app.judge.llm import client

    monkeypatch.setattr(client, "is_stub", lambda: True)
    with pytest.raises(ValueError, match="stub 模式"):
        prompt_eval.run_eval("C-3", 10, source="mock")

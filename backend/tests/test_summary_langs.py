"""反饋摘要語系 map：LLM 陣列正規化 + DTO 對表格取 zh-tw 字串（免 DB）。"""

from app.core.db._shared import attribution_dto
from app.judge.prejudge import _summary_map


def test_summary_map_array_dedup_and_zhtw() -> None:
    """LLM [{lang,text}] 陣列 → map：去重（同 lang 保第一）+ 確保含 zh-tw。"""
    m = _summary_map(
        [
            {"lang": "ja", "text": "日文摘要"},
            {"lang": "zh-tw", "text": "繁中摘要"},
            {"lang": "JA", "text": "重複應丟"},
        ]
    )
    assert m == {"ja": "日文摘要", "zh-tw": "繁中摘要"}


def test_summary_map_missing_zhtw_backfilled() -> None:
    """LLM 漏標 zh-tw → 取第一條當顯示版，保證表格恆有值。"""
    m = _summary_map([{"lang": "en", "text": "english only"}])
    assert m["zh-tw"] == "english only" and m["en"] == "english only"


def test_summary_map_string_and_empty_fallback() -> None:
    """容錯：純字串→{zh-tw:…}；空/None→{}。"""
    assert _summary_map("純字串") == {"zh-tw": "純字串"}
    assert _summary_map("") == {}
    assert _summary_map(None) == {}


def test_attribution_dto_summary_is_zhtw_string() -> None:
    """DTO content.summary＝表格用 zh-tw 字串；summary_langs＝全 map（前端零改）。"""
    d = attribution_dto({"finding_id": "x", "summary": {"zh-tw": "顯示這句", "ja": "日文版"}})
    assert d["content"]["summary"] == "顯示這句"
    assert d["content"]["summary_langs"] == {"zh-tw": "顯示這句", "ja": "日文版"}


def test_attribution_dto_summary_json_string_tolerant() -> None:
    """舊 Text 存 JSON 字串亦能解出 zh-tw（驅動差異容錯）。"""
    d = attribution_dto({"finding_id": "y", "summary": '{"zh-tw":"JSON字串版"}'})
    assert d["content"]["summary"] == "JSON字串版"

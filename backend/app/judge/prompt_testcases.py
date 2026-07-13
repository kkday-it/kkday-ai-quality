"""邊界測試集驗證 + CSV 解析（B3：mock 邊界數據上傳 → prompt 修正閉環）。

驗證對照唯一源：`prompt_source.structure()`（域機器值 + 該域 facets，與判準同源，不另存一份對照表
避免漂移）。CSV 上傳走本模組獨立輕量 parse，**不進 inbound 管線**（inbound 為 5 反饋來源 source_registry
+ source_mapping 設計，語義不合、亦免汙染）。
"""

from __future__ import annotations

import csv
import io

from app.judge import prompt_source

_POLARITIES = {"negative", "neutral", "positive"}


def _domain_values() -> set[str]:
    """合法域機器值集合（domains.json 已註冊者）。"""
    return {d["domain"] for d in prompt_source.structure()["domains"]}


def _facets_for(domain: str) -> set[str]:
    """某域合法 L2 面向 code 集合（該域 prompt 的 facet_catalog 解析結果）。"""
    for d in prompt_source.structure()["domains"]:
        if d["domain"] == domain:
            return {f["code"] for f in d["facets"]}
    return set()


def validate_row(row: dict) -> dict:
    """驗證＋正規化一筆測試 case（存檔前置閘門）。

    Args:
        row: {text, gold_l1, gold_l2?, expected_polarity?, note?, tags?}；tags 可為 list 或逗號分隔字串。

    Returns:
        正規化後的 row（去除前後空白、tags 轉 list[str]）。

    Raises:
        ValueError: text 空 / gold_l1 未在 domains.json 註冊 / gold_l2 不屬該域 facets /
            expected_polarity 非三態之一。
    """
    text_ = str(row.get("text", "")).strip()
    if not text_:
        raise ValueError("text 不可為空")

    gold_l1 = str(row.get("gold_l1", "")).strip()
    domains = _domain_values()
    if gold_l1 not in domains:
        raise ValueError(f"gold_l1 不合法：{gold_l1}（須為 {sorted(domains)} 之一）")

    gold_l2 = str(row.get("gold_l2") or "").strip()
    if gold_l2:
        facets = _facets_for(gold_l1)
        if gold_l2 not in facets:
            raise ValueError(
                f"gold_l2 不屬於域 {gold_l1}：{gold_l2}（須為 {sorted(facets)} 之一或留空）"
            )

    expected_polarity = str(row.get("expected_polarity") or "").strip()
    if expected_polarity and expected_polarity not in _POLARITIES:
        raise ValueError(
            f"expected_polarity 不合法：{expected_polarity}（須為 {sorted(_POLARITIES)} 之一或留空）"
        )

    tags = row.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]

    return {
        "text": text_,
        "gold_l1": gold_l1,
        "gold_l2": gold_l2,
        "expected_polarity": expected_polarity,
        "note": str(row.get("note") or "").strip(),
        "tags": tags,
    }


def parse_csv(content: bytes) -> tuple[list[dict], list[dict]]:
    """CSV bytes → (合法 rows, 錯誤 rows 含行號)。欄位：text,gold_l1,gold_l2,expected_polarity,note,tags。

    Args:
        content: 上傳檔案原始 bytes（utf-8-sig 容錯 Excel 存出的 BOM）。

    Returns:
        (valid, errors)：valid 為已驗證正規化的 row list；errors 為 [{row, text, error}]（行號自 2 起，
        對齊試算表標頭為第 1 行的直覺）。
    """
    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
    valid: list[dict] = []
    errors: list[dict] = []
    for i, raw in enumerate(reader, start=2):
        try:
            valid.append(validate_row(raw))
        except ValueError as e:
            errors.append({"row": i, "text": (raw.get("text") or "")[:50], "error": str(e)})
    return valid, errors

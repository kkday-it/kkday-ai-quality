"""PII 輸入端遮罩（pii_mask + prejudge._text_of 出口整合）測試。

鎖：email/台灣手機/國際碼/長數字被遮；訂單號（26KKxxx）/OID（8-9 位）/member_uuid 不誤傷；
config 停用即直通；壞 regex 不炸管線。
"""

from __future__ import annotations

import json
from pathlib import Path

from app.judge.pii_mask import mask_pii

# 直接用 seed 檔的正式規則測（SSOT；規則調整時測試自動跟上）
_CFG = json.loads(
    (Path(__file__).resolve().parents[2] / "config/ai_judge/prejudge.json").read_text()
)["pii_mask"]


def test_masks_email_phone_and_long_digits() -> None:
    text = "聯絡我 a.b-c@example.com.tw 或 0912-345-678，護照號 1234567890123456"
    out = mask_pii(text, _CFG)
    assert "[email]" in out and "example.com" not in out
    assert "[phone]" in out and "0912" not in out
    assert "[number]" in out and "1234567890123456" not in out


def test_masks_intl_phone_and_tw_with_country_code() -> None:
    out = mask_pii("call +81 9012345678 或 +886 0987654321", _CFG)
    assert "9012345678" not in out and "0987654321" not in out


def test_does_not_mask_order_no_oid_uuid() -> None:
    """訂單號/OID/member_uuid 是判決佐證與關聯鍵，不得誤傷（phone 規則鎖 10 位以上）。"""
    text = "訂單 26KK292880827 OID 47989339 商品 181723 旅客 f17ef376-817e-4a4b-a6e2-00be41d9ce7c"
    assert mask_pii(text, _CFG) == text


def test_disabled_or_missing_cfg_passthrough() -> None:
    text = "email x@y.com"
    assert mask_pii(text, {**_CFG, "enabled": False}) == text
    assert mask_pii(text, None) == text
    assert mask_pii(text, {}) == text


def test_bad_regex_rule_skipped_not_crash() -> None:
    """config 打錯 regex → 跳過該規則、其餘照套（不炸初判管線）。"""
    cfg = {
        "enabled": True,
        "rules": [
            {"name": "bad", "pattern": "([", "replacement": "[x]"},
            {"name": "email", "pattern": r"\S+@\S+", "replacement": "[email]"},
        ],
    }
    assert mask_pii("mail a@b.c ok", cfg) == "mail [email] ok"


def test_text_of_applies_mask(monkeypatch) -> None:
    """整合：_text_of 出口即遮罩（LLM 所見＝grounding 驗證基準，兩者一致）。"""
    from app.judge import prejudge

    item = {"comment": "客服都不回 email：help@kkday.com", "title": ""}
    out = prejudge._text_of(item)
    assert "[email]" in out and "help@kkday.com" not in out

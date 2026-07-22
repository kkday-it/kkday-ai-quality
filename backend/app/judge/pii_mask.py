"""PII 輸入端遮罩：初判文字送雲端 LLM 前，將個資 pattern 置換為語義佔位符。

為何存在：公司慣例「含 PII 內容外送雲端 AI API 需經資安確認」（多模態 API 先例；同團隊
另專案訂有 PII 不外送紅線）。評論/訊息自由文字偶含 email/電話/卡號——在唯一輸入出口
（prejudge._text_of）遮罩後才組 prompt，資安審查時可出示防護佐證；佔位符保留「這裡有
聯絡方式」語義，對歸因判準影響極小。

規則 SSOT＝config/ai_judge/prejudge.json `pii_mask` 節（enabled + rules[{name,pattern,replacement}]，
QC 可調；config 缺節/停用＝不遮罩）。編譯後 regex 以 pattern 字串為鍵快取（config reload 自然失效）。

刻意不遮：訂單號（26KKxxx / OID 8-9 位數字）＝判決佐證要用、非直接識別個資——phone 規則
以 10 位以上連號 + 台灣手機/國際碼格式鎖定，避免誤傷短數字串。
"""

from __future__ import annotations

import re

# 編譯快取：pattern 字串 → compiled regex（config reload 換字串即自然換鍵）
_compiled: dict[str, re.Pattern] = {}


def _regex(pattern: str) -> re.Pattern | None:
    """取編譯後 regex（無效 pattern 回 None 並跳過該規則——config 打錯不炸初判管線）。"""
    rx = _compiled.get(pattern)
    if rx is None:
        try:
            rx = re.compile(pattern)
        except re.error:
            return None
        _compiled[pattern] = rx
    return rx


def mask_pii(text: str, cfg: dict | None) -> str:
    """依 config 規則遮罩文字中的 PII pattern（純函式；cfg 缺省/停用時原樣返回）。

    Args:
        text: 初判主輸入文字（評論/訊息，可含標題行）。
        cfg: prejudge.json 的 `pii_mask` 節（{enabled, rules:[{name,pattern,replacement}]}）。

    Returns:
        遮罩後文字；enabled 非 True 或無規則時原文直通。
    """
    if not text or not cfg or not cfg.get("enabled"):
        return text
    for rule in cfg.get("rules") or []:
        pattern = rule.get("pattern") or ""
        rx = _regex(pattern)
        if rx is None:
            continue
        text = rx.sub(rule.get("replacement") or "[pii]", text)
    return text

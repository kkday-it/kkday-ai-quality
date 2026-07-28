"""AI 定點改寫：anchor 命中判定、補丁套用（含位移防護）、案例組稿。

不含 LLM 呼叫本身——這裡測的是「模型回來之後」的正確性關卡：anchor 對不上就不准套、
多處命中就不准套、套多條時位置不能互相推移（見 app/judge/prompt_reviser 模組 docstring）。
"""

from __future__ import annotations

import pytest

from app.judge import prompt_reviser as reviser

# 縮小版的目標 Prompt：故意讓「用戶不滿」在兩處出現，用來測 ambiguous
PROMPT = """# 售後根因裁判

## 常見根因情境速查
- 取消卡點：對話已明確不可退＋用戶提取消訴求 → C06。
- 憑證未到：出發前仍未收到電子票 → C01。

## 判例庫
判例一：買錯了可以退嗎 → BOT 判死 → C06「規則就是不可退用戶不滿」。
判例二：颱風停船 → 供應商主動代取消 → C06「規則就是不可退用戶不滿」的反面，走不可抗力。
"""


def test_match_status_distinguishes_unique_missing_and_duplicated() -> None:
    """唯一命中才可套用；沒逐字複製＝not_found，片段太短撞多處＝ambiguous。"""
    assert reviser.match_status(PROMPT, "對話已明確不可退＋用戶提取消訴求 → C06。") == "matched"
    # 模型「順手」把全形箭頭改成半形 → 一個字都對不上
    assert reviser.match_status(PROMPT, "對話已明確不可退+用戶提取消訴求 -> C06。") == "not_found"
    # 片段太短：判例一、判例二都有這句
    assert reviser.match_status(PROMPT, "規則就是不可退用戶不滿") == "ambiguous"


def test_annotate_patches_reports_status_and_occurrences() -> None:
    """前端據 status 決定哪幾條可勾選；occurrences 讓人看得出 ambiguous 撞了幾處。"""
    annotated = reviser.annotate_patches(
        PROMPT,
        [
            {"anchor": "→ C01。", "replacement": "→ C01（憑證類）。", "reason": "r", "risk": "k"},
            {"anchor": "規則就是不可退用戶不滿", "replacement": "x", "reason": "r", "risk": "k"},
            {"anchor": "這句原文裡沒有", "replacement": "x", "reason": "r", "risk": "k"},
        ],
    )
    assert [p["status"] for p in annotated] == ["matched", "ambiguous", "not_found"]
    assert [p["occurrences"] for p in annotated] == [1, 2, 0]
    # 欄位原樣帶回（前端要顯示理由與風險）
    assert annotated[0]["reason"] == "r" and annotated[0]["risk"] == "k"


def test_apply_patches_handles_multiple_edits_without_offset_drift() -> None:
    """一次套多條、且替換長度與原文不同時，每條都要落在正確位置（由後往前替換的意義）。"""
    revised = reviser.apply_patches(
        PROMPT,
        [
            # 靠前的一條：替換後比原文長很多
            {
                "anchor": "對話已明確不可退＋用戶提取消訴求 → C06。",
                "replacement": (
                    "對話已明確不可退＋用戶提取消訴求 → C06。"
                    "⚠️前置檢查：取消事由若被客服當場否定（本商品其實無須選日期），改判 [104] C03。"
                ),
            },
            # 靠後的一條：替換後比原文短
            {
                "anchor": "判例二：颱風停船 → 供應商主動代取消 → C06「規則就是不可退用戶不滿」的反面，走不可抗力。",
                "replacement": "判例二：颱風停船 → 走不可抗力。",
            },
        ],
    )
    assert "⚠️前置檢查：取消事由若被客服當場否定" in revised
    assert revised.rstrip().endswith("判例二：颱風停船 → 走不可抗力。")
    # 沒被指名的段落一字未動
    assert "- 憑證未到：出發前仍未收到電子票 → C01。" in revised
    assert "判例一：買錯了可以退嗎 → BOT 判死 → C06「規則就是不可退用戶不滿」。" in revised


def test_apply_patches_refuses_unmatched_and_ambiguous_anchors() -> None:
    """對不上或撞多處一律拒套（誤套會靜默改到不該改的地方，比失敗更糟）。"""
    with pytest.raises(ValueError, match="找不到"):
        reviser.apply_patches(PROMPT, [{"anchor": "原文沒有這句", "replacement": "x"}])
    with pytest.raises(ValueError, match="出現多次"):
        reviser.apply_patches(PROMPT, [{"anchor": "規則就是不可退用戶不滿", "replacement": "x"}])


def test_apply_patches_leaves_prompt_untouched_when_nothing_selected() -> None:
    """空補丁集＝原樣回（前端可能全部取消勾選）。"""
    assert reviser.apply_patches(PROMPT, []) == PROMPT


def test_format_cases_lists_only_corrected_fields_with_labels() -> None:
    """只列被標錯的欄——14 欄全列會讓真正的錯誤淹沒在判對的欄位裡。"""
    text = reviser.format_cases(
        [
            {
                "conversation": "[USER] 沒選日期就結帳了",
                "ai_output": {
                    "L2": "取消政策本身僵化",
                    "multi_issue_flag": True,
                    "sentiment": "negative",
                },
                "corrections": {"L2": "商品規格/使用規則事前確認", "multi_issue_flag": False},
                "comment": "取消事由被客服否定",
            }
        ]
    )
    assert "[USER] 沒選日期就結帳了" in text
    # 中文標籤（對齊調試台欄位卡）＋前後對照
    assert "根因分類（AI 判定，L2）" in text
    assert "取消政策本身僵化 → **商品規格/使用規則事前確認**" in text
    assert "TRUE → **FALSE**" in text  # bool 以 TRUE/FALSE 呈現，不用 Python 的 True/False
    assert "取消事由被客服否定" in text
    # sentiment 沒被標錯 → 不出現在對照清單
    assert "情緒方向" not in text


def test_format_cases_marks_all_correct_case_as_positive_example() -> None:
    """全對的案例仍要餵給模型，並明講它是正例（防過度矯正）。"""
    text = reviser.format_cases(
        [
            {
                "conversation": "[USER] 電子票還沒收到",
                "ai_output": {
                    "L1": "[104]訂單確認問題",
                    "L2": "憑證未送達",
                    "L3": "憑證送達延遲",
                },
                "corrections": {},
                "comment": "",
            }
        ]
    )
    assert "全部判對" in text and "不要把它吸走" in text
    assert "憑證未送達" in text


def test_build_user_prompt_carries_prompt_and_case_count() -> None:
    """user message 要同時帶現行全文（anchor 的複製來源）與案例。"""
    text = reviser.build_user_prompt(
        PROMPT, [{"conversation": "c", "ai_output": {}, "corrections": {}}]
    )
    assert "<current_prompt>" in text and "## 常見根因情境速查" in text
    assert "共 1 則" in text


def test_reviser_system_prompt_states_the_hard_disciplines() -> None:
    """system prompt 是這條路徑的安全帶，內容缺失等於失去防護——鎖住幾條關鍵紀律。"""
    text = reviser.reviser_system_prompt()
    assert "逐字複製" in text
    assert "唯一" in text
    assert "不得刪除" in text
    assert "1–6 條" in text


def test_patch_schema_caps_patch_count() -> None:
    """schema 層也擋補丁數量（不只靠 prompt 自律）。"""
    assert PATCH_MAX == reviser.PATCH_SCHEMA["properties"]["patches"]["maxItems"]


PATCH_MAX = reviser.MAX_PATCHES

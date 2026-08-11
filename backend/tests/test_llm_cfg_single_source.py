"""LLM 呼叫 cfg 的單一組裝出口與 provider 判別軸回歸鎖。

2026-08-11 實測缺陷：`{token, base_url, model, temperature, thinking, reasoning_effort,
service_tier}` 這組 cfg 在四個模組各手寫一份（prompt_debug / prompt_reviser /
prompt_debug_batch / prompt_regression），且**四份都漏帶 `provider`**。

漏它的後果不是「少一個欄位」：`effective_llm_dict()` 明確回傳 provider，它才是供應商判別的
主軸；缺了它，降級階梯 `prompt_debug._request_compat` 與 `client.can_use_responses_api()`
只能退回從 `base_url` 反推，而 `provider_id_for()` 對未知端點一律回退 openai——
**自訂 gateway 上的 bytedance 因此被判成 openai，整條相容降級被跳過、400 硬失敗**。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.core import settings as app_settings
from app.judge.llm import client

_APP = Path(__file__).resolve().parents[1] / "app"
_CFG_CONSUMERS = (
    "judge/prompt_debug.py",
    "judge/prompt_reviser.py",
    "judge/prompt_debug_batch.py",
    "judge/prompt_regression.py",
)


def test_cfg_carries_provider() -> None:
    """單一出口組出的 cfg 必須帶 provider——這是判別軸，不是可選欄。"""
    cfg = client.cfg_from_effective(
        {
            "base_url": "https://my-gateway.internal/v1",
            "model": "seed-2-0-lite",
            "provider": "bytedance",
        }
    )
    assert cfg["provider"] == "bytedance"
    assert set(cfg) == {
        "token",
        "base_url",
        "model",
        "provider",
        "temperature",
        "thinking",
        "reasoning_effort",
        "service_tier",
    }


def test_provider_wins_over_base_url_reverse_lookup() -> None:
    """自訂 gateway 上的非 OpenAI 供應商：反推會誤判 openai，帶了 provider 就不會。

    這正是缺陷的重現路徑——`provider_id_for` 對未知端點回退 openai 是**刻意**的
    （未知相容端點多半是 OpenAI 相容層），所以不能靠改它來修，只能讓 cfg 自帶 provider。
    """
    url = "https://my-gateway.internal/v1"
    assert app_settings.provider_id_for(url) == "openai", "前提：未知端點反推確實回退 openai"
    cfg = client.cfg_from_effective({"base_url": url, "model": "m", "provider": "gemini"})
    # gemini 的 OpenAI 相容層沒有 /responses；靠反推會誤判為有，導致 404 而非可攔的 400
    assert client.can_use_responses_api(cfg, {}) is False


@pytest.mark.parametrize("rel", _CFG_CONSUMERS)
def test_no_module_hand_rolls_cfg(rel: str) -> None:
    """四個消費模組不得再手寫 cfg 字面量——新增 per-call 旋鈕只該改一處。"""
    src = (_APP / rel).read_text(encoding="utf-8")
    assert '"service_tier"' not in src or "cfg_from_effective" in src, (
        f"{rel} 疑似仍手寫 cfg；請改用 client.cfg_from_effective()"
    )
    hand_rolled = re.search(r'"reasoning_effort":\s*effective\.get\(', src)
    assert not hand_rolled, f"{rel} 仍手寫 cfg 組裝——請改用 client.cfg_from_effective()"

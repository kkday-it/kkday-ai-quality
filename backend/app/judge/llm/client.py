"""LLM client + stub 開關。

無 OPENAI_API_KEY → stub 模式（啟發式，零 key 走通 pipeline）；
key 到位（6/25）→ OpenAI SDK 真判（gpt-5-mini）。
"""

from __future__ import annotations

import json
import os


def has_key() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY"))


def is_stub() -> bool:
    return not has_key()


def chat_json(system: str, user: str) -> dict:
    """真 LLM 結構化呼叫（key 到位後用）。"""
    from openai import OpenAI

    client = OpenAI()
    model = os.environ.get("AI_JUDGE_MODEL", "gpt-5-mini")
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        response_format={"type": "json_object"},
    )
    return json.loads(resp.choices[0].message.content or "{}")

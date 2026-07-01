"""promptfoo Python provider：把 review 文字丟進真 prejudge 引擎，回傳判到的 l3_code。

回歸測試用——直接呼叫 app/judge/prejudge.to_finding（複用線上判準，不另寫一套分類邏輯）。
需 backend venv 可 import app.*，且有一組真 token 的 user（否則走 stub，判準無意義）。
選 user：環境變數 PROMPTFOO_USER_ID 指定；未設則取第一個有 provider_token 的 user。

跑法見同目錄 README.md。
"""

from __future__ import annotations

import json
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(_ROOT, "backend"))

_ready = False


def _init() -> None:
    """一次性注入生效 LLM 設定（token）供 judge contextvar 讀取。"""
    global _ready
    if _ready:
        return
    from sqlalchemy import select

    from app.core import settings as app_settings
    from app.core import tables as T

    uid = os.environ.get("PROMPTFOO_USER_ID")
    if not uid:
        with T.get_engine().connect() as c:
            for r in c.execute(
                select(T.user_settings.c.user_id, T.user_settings.c.data)
            ).mappings():
                d = json.loads(r["data"] or "{}")
                if any((d.get("provider_tokens") or {}).values()):
                    uid = r["user_id"]
                    break
    app_settings.set_current(app_settings.effective_llm_dict(app_settings.load_settings(uid)))
    _ready = True


def call_api(prompt, options, context):
    """promptfoo 呼叫入口：prompt＝review 文字 → 回 {output: 判到的 l3_code 或 ABSTAIN}。"""
    _init()
    from app.core import settings as app_settings
    from app.judge import prejudge

    item = {"item_id": "promptfoo", "comment": prompt, "rating": 1, "raw": {}}
    f = prejudge.to_finding(item, model=app_settings.current().get("model"))
    return {"output": f.l3_code or "ABSTAIN"}

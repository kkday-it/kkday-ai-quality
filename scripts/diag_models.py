"""模型連線診斷：逐一 ping config/defaults.json 各 provider 的 defaultModels。

用「DB 內已存的真 token」逐一打最小 prompt，回 OK / FAIL + latency + error。
token 絕不輸出（只顯示長度/前後綴）。
執行：cd backend && .venv/bin/python ../scripts/diag_models.py
"""

from __future__ import annotations

import sqlite3
import time

from app.core import settings as app_settings
from app.core.db import DB_PATH


def _pick_token_user() -> tuple[str, dict]:
    """挑「有真實 token」的 user 設定（token 最長者視為真）。"""
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    best: tuple[int, str, dict] = (-1, "", {})
    for r in c.execute("SELECT user_id FROM user_settings").fetchall():
        cfg = app_settings.load_settings(r["user_id"])
        tok = cfg.get("api_token") or ""
        if len(tok) > best[0]:
            best = (len(tok), r["user_id"], cfg)
    return best[1], best[2]


def _ping(base_url: str, token: str, model: str) -> dict:
    from openai import OpenAI

    client = OpenAI(api_key=token, base_url=base_url) if base_url else OpenAI(api_key=token)
    t0 = time.monotonic()
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "你是連線測試助手，只需極簡短回覆。"},
                {"role": "user", "content": "回覆 OK"},
            ],
        )
        dt = int((time.monotonic() - t0) * 1000)
        usage = getattr(resp, "usage", None)
        reply = (resp.choices[0].message.content or "").strip() if resp.choices else ""
        return {
            "ok": True,
            "latency_ms": dt,
            "reply": reply[:40],
            "tokens": getattr(usage, "total_tokens", None) if usage else None,
        }
    except Exception as e:  # noqa: BLE001 — 診斷需收所有錯
        dt = int((time.monotonic() - t0) * 1000)
        return {"ok": False, "latency_ms": dt, "error": str(e).splitlines()[0][:160]}


def main() -> None:
    user_id, cfg = _pick_token_user()
    token = cfg.get("api_token") or ""
    masked = (token[:7] + "…" + token[-4:]) if len(token) > 12 else "(無)"
    print(f"使用 user={user_id}  token={masked} ({len(token)} chars)")
    print("=" * 78)

    providers = app_settings.LLM_PROVIDERS
    for prov in providers:
        pid = prov.get("id")
        base = prov.get("base_url") or ""
        models = prov.get("defaultModels", [])
        own_token = pid == cfg.get("provider")  # 此 provider 是否就是 token 所屬
        print(f"\n▌ {prov.get('label')} [{pid}]  base_url={base}")
        if not own_token:
            print(f"  ⚠ 此 provider 非當前 token 所屬（token=openai），預期 401/auth 失敗")
        for m in models:
            r = _ping(base, token, m)
            if r["ok"]:
                tok = f" tokens={r['tokens']}" if r.get("tokens") else ""
                print(f"  ✅ {m:<16} {r['latency_ms']:>6}ms  reply={r['reply']!r}{tok}")
            else:
                print(f"  ❌ {m:<16} {r['latency_ms']:>6}ms  {r['error']}")


if __name__ == "__main__":
    main()

"""模型連線診斷（per-provider token 版）：逐一 ping 各 provider 的 defaultModels。

用 DB user_settings.provider_tokens 各 provider 自己的 token 測連線，回 OK / FAIL + latency。
token 絕不輸出（只顯示長度/遮罩）。
執行：cd backend && .venv/bin/python ../scripts/diag_models.py
可選：指定 user_id 為第一參數；預設挑 provider_tokens 最多者。
"""

from __future__ import annotations

import sqlite3
import sys
import time

from app.core import settings as app_settings
from app.core.db import DB_PATH


def _pick_user(arg: str | None) -> tuple[str, dict]:
    if arg:
        return arg, app_settings.load_settings(arg)
    c = sqlite3.connect(DB_PATH)
    best: tuple[int, str, dict] = (-1, "", {})
    for r in c.execute("SELECT user_id FROM user_settings").fetchall():
        cfg = app_settings.load_settings(r[0])
        n = len(cfg.get("provider_tokens") or {})
        if n > best[0]:
            best = (n, r[0], cfg)
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
    uid, cfg = _pick_user(sys.argv[1] if len(sys.argv) > 1 else None)
    ptokens = cfg.get("provider_tokens") or {}
    print(f"使用 user={uid}  已設定 token 的 provider={list(ptokens)}")
    print("=" * 78)

    for prov in app_settings.LLM_PROVIDERS:
        pid = prov.get("id")
        base = prov.get("base_url") or ""
        token = ptokens.get(pid, "")
        print(f"\n▌ {prov.get('label')} [{pid}]  base_url={base}")
        if not token:
            print("  ⚠ 此 provider 無 token，跳過")
            continue
        for m in prov.get("defaultModels", []):
            r = _ping(base, token, m)
            if r["ok"]:
                tok = f" tokens={r['tokens']}" if r.get("tokens") else ""
                print(f"  ✅ {m:<22} {r['latency_ms']:>6}ms  reply={r['reply']!r}{tok}")
            else:
                print(f"  ❌ {m:<22} {r['latency_ms']:>6}ms  {r['error']}")


if __name__ == "__main__":
    main()

"""llm_area_defaults 扁平旋鈕 → per-provider 巢狀（一區每家各一組）

舊形狀 `{area: {provider, model, thinking, reasoning_effort, temperature}}` 一個功能區只裝得下
一個供應商的旋鈕，於是前端三個供應商 tab 互相覆蓋：存了 openai 就把 bytedance／gemini 的設定
整包沖掉，切回去只拿得到出廠預設——使用者以為存了三份，實際庫裡永遠只有最後存的那一份。

新形狀把「當前選定哪家」與「各家各自的旋鈕」分開：

    {area: {"provider": 當前選定, "knobs": {provider_id: {model, thinking, ...}}}}

本遷移把既有那組旋鈕歸到它原本的 provider 名下，其餘供應商留空（讀取時走該區×該家的 seed）。
**使用者當前生效的配置完全不變**，只是多了另外兩家的存放空間。

刻意做成一次性 DDL 而非讀取時自癒：專案核心原則「退役即徹底」——舊形狀在程式碼中零殘留，
不留 `if "knobs" not in cfg` 這類相容分支（兩套形狀並存正是本專案踩過的坑）。

只重塑 `llm_area_defaults` 一個 key：`llm_tokens` / `qc_passwords` 是 at-rest 密文，
逐位元組原樣搬運，不解密、不重寫。

Revision ID: a7c4e91b3d08
Revises: f3a81c6e5d92
Create Date: 2026-07-30
"""

from __future__ import annotations

import json

import sqlalchemy as sa

from alembic import op

revision = "a7c4e91b3d08"
down_revision = "f3a81c6e5d92"
branch_labels = None
depends_on = None

# 舊 row 沒存 provider 時的歸屬（與 settings._DEFAULT_LLM["provider"] 一致）
_FALLBACK_PROVIDER = "openai"


def _rows(conn) -> list[tuple[str, str]]:
    """取所有 settings row 的 (key, data)；data 為 JSON 文字。"""
    return list(conn.execute(sa.text("SELECT key, data FROM settings")))


def _save(conn, key: str, payload: dict) -> None:
    conn.execute(
        sa.text("UPDATE settings SET data = :d WHERE key = :k"),
        {"d": json.dumps(payload, ensure_ascii=False), "k": key},
    )


def upgrade() -> None:
    conn = op.get_bind()
    for key, raw in _rows(conn):
        try:
            payload = json.loads(raw or "{}")
        except (TypeError, ValueError):
            continue  # 壞 row 不動它，讓問題留在原地可被發現，勿靜默改寫
        areas = payload.get("llm_area_defaults")
        if not isinstance(areas, dict):
            continue

        changed = False
        for area, cfg in list(areas.items()):
            if not isinstance(cfg, dict) or "knobs" in cfg:
                continue  # 已是新形狀 → 跳過（冪等）
            provider = cfg.get("provider") or _FALLBACK_PROVIDER
            knobs = {k: v for k, v in cfg.items() if k != "provider"}
            areas[area] = {
                "provider": provider,
                "knobs": {provider: knobs} if knobs else {},
            }
            changed = True

        if changed:
            _save(conn, key, payload)


def downgrade() -> None:
    """攤回扁平：只保留「當前選定供應商」那一組旋鈕，其餘供應商的設定會遺失。

    這是有損降級——舊形狀本來就裝不下多家旋鈕，故無法無損還原。
    """
    conn = op.get_bind()
    for key, raw in _rows(conn):
        try:
            payload = json.loads(raw or "{}")
        except (TypeError, ValueError):
            continue
        areas = payload.get("llm_area_defaults")
        if not isinstance(areas, dict):
            continue

        changed = False
        for area, cfg in list(areas.items()):
            if not isinstance(cfg, dict) or "knobs" not in cfg:
                continue
            provider = cfg.get("provider") or _FALLBACK_PROVIDER
            knobs = dict((cfg.get("knobs") or {}).get(provider) or {})
            areas[area] = {"provider": provider, **knobs}
            changed = True

        if changed:
            _save(conn, key, payload)

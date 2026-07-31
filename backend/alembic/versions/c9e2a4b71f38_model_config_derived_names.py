"""模型配置改為「名稱＝規格」：補入預設配置、折疊惰性旋鈕、移除 name 欄

配置名稱不再由使用者自取，改由規格衍生（`settings.derive_config_name`），唯一性也從「名稱不重複」
改成「規格不重複」。既有列因此要做三件事：

1. **補入預設配置**。`_blank_settings()` 帶的預設內容只對「`llm_model_configs` 這個 key 不存在」
   的 row 生效（`load_settings` 是 `{**_blank_settings(), **data}`），既有 row 帶著自己的清單，
   永遠不會被種入。而新的寫入校驗要求「`areaDefaults` 指向的 id 必須存在」——不補的話，使用者
   下一次任何儲存都會直接 400。**預設配置先放**，保證那幾個 id 必然存在。
2. **折疊惰性旋鈕**。openai/gemini 不讀 thinking、bytedance 在 thinking=disabled/auto 下不送
   reasoning_effort（見 `client._reasoning_kwargs`）；折疊後「庫內值 ≡ 規格鍵 ≡ 衍生名」三者恆等。
   規格與既有列撞號者丟棄（先到先得），因為撞號本來就代表它們是同一筆配置。
3. **移除 `name` 欄**。名稱改為讀取端衍生的純投影欄，落庫的 name 只會與規格漂移。

能力片段（各 provider 的 `thinkingControl`）與預設配置**凍結在本檔內**，不 import 活的
`settings.spec_key`：遷移行為不該隨日後改 `llm_model.json` 而變（比照 `b8d3f5a91c02` 的
`_SEED_SIGNATURES` 作法）。

`downgrade()` 為 no-op：折疊掉的惰性旋鈕與被丟棄的重複列都無從還原，且還原後的狀態在新版
校驗下存不回去。硬要回退請改用 pg_dump 備份。

Revision ID: c9e2a4b71f38
Revises: b8d3f5a91c02
Create Date: 2026-07-31
"""

from __future__ import annotations

import json
import logging
import uuid

import sqlalchemy as sa

from alembic import op

revision = "c9e2a4b71f38"
down_revision = "b8d3f5a91c02"
branch_labels = None
depends_on = None

_log = logging.getLogger("alembic.runtime.migration")

# 凍結：各 provider 的 thinking 控制形態（對齊本次 config/global/llm_model.json 的 providers[]）
_NATIVE_SWITCH_PROVIDERS = frozenset({"bytedance"})

# 凍結：預設模型配置（對齊本次 llm_model.json 的 modelConfigs；刻意不含 name——新制不落庫）
_DEFAULT_CONFIGS: tuple[dict, ...] = (
    {
        "id": "seed-openai-balanced",
        "provider": "openai",
        "model": "gpt-5.4-mini",
        "thinking": "default",
        "reasoning_effort": "medium",
        "temperature": None,
    },
    {
        "id": "seed-openai-flagship",
        "provider": "openai",
        "model": "gpt-5.5",
        "thinking": "default",
        "reasoning_effort": "high",
        "temperature": None,
    },
    {
        "id": "seed-gemini-balanced",
        "provider": "gemini",
        "model": "gemini-3.5-flash",
        "thinking": "default",
        "reasoning_effort": "medium",
        "temperature": None,
    },
    {
        "id": "seed-bytedance-balanced",
        "provider": "bytedance",
        "model": "seed-2-0-lite-260228",
        "thinking": "default",
        "reasoning_effort": "medium",
        "temperature": None,
    },
)

# thinking 舊值域 → 當前值域（極舊環境可能還留著 on/off）
_LEGACY_THINKING = {"on": "enabled", "off": "disabled"}


def _rows(conn) -> list[tuple[str, str]]:
    """取所有 settings row 的 (key, data)；data 為 JSON 文字。"""
    return list(conn.execute(sa.text("SELECT key, data FROM settings")))


def _save(conn, key: str, payload: dict) -> None:
    conn.execute(
        sa.text("UPDATE settings SET data = :d WHERE key = :k"),
        {"d": json.dumps(payload, ensure_ascii=False), "k": key},
    )


def _spec_key(cfg: dict) -> tuple:
    """凍結版的規格鍵：折疊惰性旋鈕 + temperature round(2)（語義對齊 settings.spec_key）。"""
    provider = str(cfg.get("provider") or "").strip()
    model = str(cfg.get("model") or "").strip()
    thinking = str(cfg.get("thinking") or "default")
    thinking = _LEGACY_THINKING.get(thinking, thinking)
    effort = str(cfg.get("reasoning_effort") or "default")

    if provider not in _NATIVE_SWITCH_PROVIDERS:
        thinking = "default"  # R1：effortOnly 供應商不讀 thinking
    elif thinking in ("disabled", "auto"):
        effort = "default"  # R2：Ark 在這兩態下不送 reasoning_effort

    temp = cfg.get("temperature")
    try:
        temperature = None if temp is None else round(float(temp), 2)
    except (TypeError, ValueError):
        temperature = None
    return (provider, model, thinking, effort, temperature)


def _normalized(cfg: dict, cfg_id: str) -> dict:
    """折疊後的落庫形狀（無 name 欄）。"""
    provider, model, thinking, effort, temperature = _spec_key(cfg)
    return {
        "id": cfg_id,
        "provider": provider,
        "model": model,
        "thinking": thinking,
        "reasoning_effort": effort,
        "temperature": temperature,
    }


def upgrade() -> None:
    conn = op.get_bind()
    for key, raw in _rows(conn):
        try:
            payload = json.loads(raw or "{}")
        except (TypeError, ValueError):
            continue  # 壞 row 不動它，讓問題留在原地可被發現，勿靜默改寫
        existing = payload.get("llm_model_configs")
        if not isinstance(existing, list):
            continue  # 沒這個 key＝全新 row，_blank_settings() 會種入預設，不需遷移

        # ① 預設配置先放（順序不可顛倒——保證 areaDefaults 指向的 id 必然存在）
        out = [dict(c) for c in _DEFAULT_CONFIGS]
        seen = {_spec_key(c) for c in out}
        ids = {c["id"] for c in out}

        # ② 既有列後放：折疊後與已佔用規格撞號者丟棄（撞號＝本來就是同一筆配置）
        dropped: list[str] = []
        for cfg in existing:
            if not isinstance(cfg, dict):
                continue
            spec = _spec_key(cfg)
            if not spec[0] or not spec[1]:
                dropped.append(str(cfg.get("id") or "?"))
                continue  # 缺 provider/model 的壞列
            cfg_id = str(cfg.get("id") or "").strip() or str(uuid.uuid4())
            if spec in seen or cfg_id in ids:
                dropped.append(cfg_id)
                continue
            seen.add(spec)
            ids.add(cfg_id)
            out.append(_normalized(cfg, cfg_id))

        payload["llm_model_configs"] = out
        _save(conn, key, payload)
        if dropped:
            # 被丟棄的 id 可能還被某人的 localStorage 指著；記進 log 供對照（回落機制會接住，
            # 使用者只會看到該功能區回到預設起點，不會壞掉）
            _log.warning(
                "settings[%s]：模型配置規格重複，丟棄 %d 筆 id=%s", key, len(dropped), dropped
            )


def downgrade() -> None:
    """no-op：折疊掉的惰性旋鈕與丟棄的重複列都無從還原，且還原後在新版校驗下存不回去。"""
    _log.warning("c9e2a4b71f38 為單向遷移，downgrade 不做任何事；需回退請用 pg_dump 備份還原。")

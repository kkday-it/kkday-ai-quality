"""llm_area_defaults（區×供應商巢狀旋鈕）→ llm_model_configs（全域具名配置庫）

舊形狀把「旋鈕」綁死在功能區底下：同一組 gpt-5.4-mini/medium 要在 5 個功能區各調一次、調完
沒有名字，事後看跑批紀錄只有一串 model 名，講不清「那批是用哪個設定跑的」。新形狀把旋鈕升格為
**全域具名配置**，一筆可同時被多個功能區引用：

    {"llm_model_configs": [{id, name, provider, model, thinking, reasoning_effort, temperature}]}

⚠️ **「哪個功能區用哪一筆」不在 DB**，故本遷移也不產生任何 area→config 映射。那是個人選擇
（一個人切配置不該讓全團隊跟著變），改存前端 localStorage。
⚠️ **該決定於 2026-07-31 被推翻**：綁定已收回 DB `settings.llm_area_configs`（area → config id，團隊共用單一份）——瀏覽器儲存跨不了人與裝置。本遷移只負責拆掉舊的 `llm_area_defaults`（存的是旋鈕不是 id），新 key 由 `_blank_settings()` 補空 dict，不需要遷移。
副作用是升級後各功能區的選擇會**重置回出廠預設一次**——server 端遷移寫不到瀏覽器。使用者原本的
旋鈕沒有遺失，它們以具名配置的形式出現在設定面板，重選一次即可。刻意不做「遷移寫 hint、前端首次
載入回填」的補償：那是相容分支，專案核心原則「退役即徹底」不留。

轉換規則：
- 每個非空 `knobs[provider_id]` 轉成一筆配置（含「存過但當前未選中」的供應商——那是使用者真的
  做過的設定，不因當下沒被選中就丟棄；配置庫本來就允許存在但暫時沒人用）。
- 以 (provider, model, thinking, reasoning_effort, temperature) 去重：不去重的話「同一組
  openai+gpt-5.4-mini+medium」會在 5 區各存一份，配置庫一開場就髒。
- 等值於出廠種子者直接跳過不建（該區的綁定自然落回種子）。種子簽章**凍結在本檔**而非讀
  `llm_model.json`：遷移的行為不該隨日後改配置而變。
- 自動命名 `{model} · {effort}`，撞名補 `(2)`。刻意不把來源區名嵌進名字——具名配置是全域共用物件，
  名字寫死來源區會誤導使用者以為它「屬於」某區。

只重塑 `llm_area_defaults` → `llm_model_configs` 兩個 key：`llm_tokens` / `qc_passwords` 是
at-rest 密文，逐位元組原樣搬運，不解密、不重寫。

Revision ID: b8d3f5a91c02
Revises: a7c4e91b3d08
Create Date: 2026-07-31
"""

from __future__ import annotations

import json
import uuid

import sqlalchemy as sa

from alembic import op

revision = "b8d3f5a91c02"
down_revision = "a7c4e91b3d08"
branch_labels = None
depends_on = None

# 舊 row 的 knobs map 沒有 provider key 時的歸屬（與 settings._DEFAULT_LLM["provider"] 一致）
_FALLBACK_PROVIDER = "openai"

# thinking 舊值域 → 當前值域（對齊 settings._LEGACY_THINKING_MODES）。
# 極舊環境可能還留著 on/off，直接搬進新結構會在下次寫入時被新的值域校驗擋下。
_LEGACY_THINKING = {"on": "enabled", "off": "disabled"}

# 出廠種子的**簽章**（凍結快照，對齊本次的 config/global/llm_model.json `modelConfigs`）。
# 與種子等值的舊旋鈕不必再建一筆自訂配置——生效清單本來就含種子。
_SEED_SIGNATURES: frozenset[tuple] = frozenset(
    {
        ("openai", "gpt-5.4-mini", "default", "medium", None),
        ("openai", "gpt-5.5", "default", "high", None),
        ("gemini", "gemini-3.5-flash", "default", "medium", None),
        ("bytedance", "seed-2-0-lite-260228", "default", "medium", None),
    }
)
# 種子名（同上，凍結）：自動命名不得與之相撞，否則新配置一存進去就被唯一性校驗擋下。
_SEED_NAMES: frozenset[str] = frozenset(
    {"openai · 均衡", "openai · 旗艦", "gemini · 均衡", "bytedance · 均衡"}
)


def _rows(conn) -> list[tuple[str, str]]:
    """取所有 settings row 的 (key, data)；data 為 JSON 文字。"""
    return list(conn.execute(sa.text("SELECT key, data FROM settings")))


def _save(conn, key: str, payload: dict) -> None:
    conn.execute(
        sa.text("UPDATE settings SET data = :d WHERE key = :k"),
        {"d": json.dumps(payload, ensure_ascii=False), "k": key},
    )


def _signature(provider: str, knobs: dict) -> tuple | None:
    """把一組舊旋鈕正規化成去重簽章；缺 model（無從執行）回 None 表示不值得轉。"""
    model = str(knobs.get("model") or "").strip()
    if not model:
        return None
    thinking = str(knobs.get("thinking") or "default")
    temperature = knobs.get("temperature")
    return (
        provider,
        model,
        _LEGACY_THINKING.get(thinking, thinking),
        str(knobs.get("reasoning_effort") or "default"),
        # 1 與 1.0 是同一個溫度，不正規化會被當成兩筆不同配置
        float(temperature) if isinstance(temperature, (int, float)) else None,
    )


def _unique_name(model: str, effort: str, taken: set[str]) -> str:
    """`{model} · {effort}` 為底（effort 是 default 時只用 model），撞名補 `(2)`、`(3)`…"""
    base = model if effort in ("", "default") else f"{model} · {effort}"
    name = base
    n = 2
    while name.casefold() in taken:
        name = f"{base} ({n})"
        n += 1
    taken.add(name.casefold())
    return name


def _flatten_to_area_defaults(configs: list) -> dict:
    """降級用：配置庫 → 舊的 `{area: {provider, knobs: {provider_id: 旋鈕}}}` 巢狀形狀。

    抽成純函式而非寫死在 `downgrade()` 裡，是為了能在不碰真實 settings 列的前提下驗證降級行為
    （降級有損，拿真實列試會毀掉使用者的配置）。

    Args:
        configs: `llm_model_configs` 陣列。

    Returns:
        舊形狀 dict；每個供應商只取第一筆配置（舊形狀一區一家裝不下更多）。
    """
    knobs_by_provider: dict[str, dict] = {}
    for cfg in configs:
        if not isinstance(cfg, dict):
            continue
        provider = str(cfg.get("provider") or _FALLBACK_PROVIDER)
        if provider in knobs_by_provider:
            continue  # 一家只留第一筆（舊形狀裝不下更多）
        knobs_by_provider[provider] = {
            "model": cfg.get("model", ""),
            "thinking": cfg.get("thinking", "default"),
            "reasoning_effort": cfg.get("reasoning_effort", "default"),
            "temperature": cfg.get("temperature"),
        }

    first_provider = next(iter(knobs_by_provider), _FALLBACK_PROVIDER)
    # 遷移須自足（不 import app.*），故用本 revision 當時的固定四區清單
    return {
        area: {
            "provider": first_provider,
            "knobs": {p: dict(k) for p, k in knobs_by_provider.items()},
        }
        for area in ("prejudge", "prompt_debug", "sandbox", "prompt_revise")
    }


def upgrade() -> None:
    conn = op.get_bind()
    for key, raw in _rows(conn):
        try:
            payload = json.loads(raw or "{}")
        except (TypeError, ValueError):
            continue  # 壞 row 不動它，讓問題留在原地可被發現，勿靜默改寫
        areas = payload.get("llm_area_defaults")
        if not isinstance(areas, dict):
            continue  # 沒這個 key＝已遷移或全新 row（冪等）

        taken_names = {n for n in _SEED_NAMES}
        seen: set[tuple] = set()
        configs: list[dict] = []
        # 依 area 名排序：dict 迭代序不保證穩定，排序讓同一份輸入永遠產出同一組命名（含撞名尾碼）
        for _area, cfg in sorted(areas.items()):
            if not isinstance(cfg, dict):
                continue
            for provider, knobs in sorted((cfg.get("knobs") or {}).items()):
                if not isinstance(knobs, dict):
                    continue
                sig = _signature(provider or _FALLBACK_PROVIDER, knobs)
                if sig is None or sig in seen or sig in _SEED_SIGNATURES:
                    continue
                seen.add(sig)
                p, model, thinking, effort, temperature = sig
                configs.append(
                    {
                        "id": str(uuid.uuid4()),
                        "name": _unique_name(model, effort, taken_names),
                        "provider": p,
                        "model": model,
                        "thinking": thinking,
                        "reasoning_effort": effort,
                        "temperature": temperature,
                    }
                )

        # 既有自訂配置在前（理論上不該有——本 revision 才引入該 key），轉出來的接在後面
        payload["llm_model_configs"] = [*(payload.get("llm_model_configs") or []), *configs]
        payload.pop("llm_area_defaults")  # 退役即徹底：不留空 dict，直接沒有這個 key
        _save(conn, key, payload)


def downgrade() -> None:
    """有損降級：把配置庫攤回舊的區×供應商巢狀形狀，每區每家取第一筆同供應商的配置。

    無法無損還原的地方（照實列出，不假裝可逆）：
    - **配置名稱全部遺失**——舊形狀沒有存名字的地方。
    - **同一供應商的多筆配置只留第一筆**——舊形狀一區一家只裝得下一組旋鈕。
    - **各區原本選了哪一筆無從還原**——本遷移執行當下那份在 localStorage，不在 DB；降級後每區的
      `provider` 一律取第一筆配置的供應商。
    """
    conn = op.get_bind()
    for key, raw in _rows(conn):
        try:
            payload = json.loads(raw or "{}")
        except (TypeError, ValueError):
            continue
        configs = payload.get("llm_model_configs")
        if not isinstance(configs, list):
            continue
        payload["llm_area_defaults"] = _flatten_to_area_defaults(configs)
        payload.pop("llm_model_configs")
        _save(conn, key, payload)

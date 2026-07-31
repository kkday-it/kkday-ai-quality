"""折疊「送了也沒用」的 temperature（依 2026-07-31 逐 model 實測）

配置名稱＝規格之後，庫裡出現了功能完全相同卻並存為兩列的配置：
`OpenAI · gpt-5.4-mini · medium · temp:1` 與 `OpenAI · gpt-5.4-mini · medium` 送出的 payload
一模一樣（該組合下 OpenAI 只接受預設溫度 1，「送 1」與「不送」等價），`· temp:1` 這個後綴還暗示
了一個並不存在的差異。這違反「規格相同的配置只能有一筆」。

修法＝把 `spec_key` 的 R3 從「一律不折」改成「**送了也沒用時才折**」，並在此把既有資料一次正規化。

判定依據是 2026-07-31 的逐 model 實測（144 次真實 API 呼叫，繞過 client 的自動降級直看原始回應）：
- `gpt-5-mini` / `gpt-5.5`：任何狀態送自訂溫度都被拒（`Only the default (1) value is supported`）
- `gpt-5.4-mini` / `gpt-5.4`：只有推理生效（reasoning_effort 非 none/default）時被拒
- ByteDance `seed-*`：**送 0.3 正常受理**、送 99/-5 才被 `InvalidParameter` 拒 → 伺服器真的會採用，
  對它們折疊會改變送出內容，**故不折**（同批實測也推翻了原本 `temperatureAlwaysLocked: true` 的宣告）

能力片段**凍結在本檔內**，不 import 活的 `settings`：遷移行為不該隨日後改 `llm_model.json` 而變
（比照 `b8d3f5a91c02` / `c9e2a4b71f38` 的既有作法）。

`downgrade()` 為 no-op：被折掉的值無從還原（原值可能是任何數字，不必然是 lockedTemperatureValue），
且合併掉的重複列也回不來。

Revision ID: d1f7a3c8e520
Revises: c9e2a4b71f38
Create Date: 2026-07-31
"""

from __future__ import annotations

import json
import logging

import sqlalchemy as sa

from alembic import op

revision = "d1f7a3c8e520"
down_revision = "c9e2a4b71f38"
branch_labels = None
depends_on = None

_log = logging.getLogger("alembic.runtime.migration")

# 凍結：nativeSwitch 供應商（thinking 為原生開關；其餘為 effortOnly）
_NATIVE_SWITCH_PROVIDERS = frozenset({"bytedance"})

# 凍結：2026-07-31 實測的 temperature 鎖定狀態。
#   "always"      → 任何狀態都只接受預設值
#   "when_thinking" → 只有推理生效時只接受預設值
# 未列出的 model 走 `_PROVIDER_TEMP_LOCK` 的 provider 級後備（**不可省**：openai provider 級本身就是
# when_thinking，只列 model 名會讓自訂／未登記的 openai model 在遷移時漏折，與 live 判定分歧）。
_TEMP_LOCK: dict[str, str] = {
    "gpt-5-mini": "always",
    "gpt-5.5": "always",
    "gpt-5.4-mini": "when_thinking",
    "gpt-5.4": "when_thinking",
}


# provider 級 temperature 鎖定後備（對齊本次 llm_model.json 的 providers[]）
_PROVIDER_TEMP_LOCK: dict[str, str] = {"openai": "when_thinking"}


def _rows(conn) -> list[tuple[str, str]]:
    return list(conn.execute(sa.text("SELECT key, data FROM settings")))


def _save(conn, key: str, payload: dict) -> None:
    conn.execute(
        sa.text("UPDATE settings SET data = :d WHERE key = :k"),
        {"d": json.dumps(payload, ensure_ascii=False), "k": key},
    )


def _spec_key(cfg: dict) -> tuple:
    """凍結版規格鍵：R1/R2 折惰性旋鈕 + R3 折惰性 temperature（語義對齊 settings.spec_key）。"""
    provider = str(cfg.get("provider") or "").strip()
    model = str(cfg.get("model") or "").strip()
    thinking = str(cfg.get("thinking") or "default")
    effort = str(cfg.get("reasoning_effort") or "default")

    native = provider in _NATIVE_SWITCH_PROVIDERS
    if not native:
        thinking = "default"  # R1
    elif thinking in ("disabled", "auto"):
        effort = "default"  # R2

    temp = cfg.get("temperature")
    try:
        temperature = None if temp is None else round(float(temp), 2)
    except (TypeError, ValueError):
        temperature = None

    if temperature is not None:
        lock = _TEMP_LOCK.get(model) or _PROVIDER_TEMP_LOCK.get(provider)
        reasoning_active = (
            thinking in ("enabled", "auto") if native else effort not in ("none", "default")
        )
        if lock == "always" or (lock == "when_thinking" and reasoning_active):
            temperature = None  # R3：送了也沒用 → 不是規格的一部分
    return (provider, model, thinking, effort, temperature)


def upgrade() -> None:
    conn = op.get_bind()
    for key, raw in _rows(conn):
        try:
            payload = json.loads(raw or "{}")
        except (TypeError, ValueError):
            continue  # 壞 row 不動它，讓問題留在原地可被發現
        configs = payload.get("llm_model_configs")
        if not isinstance(configs, list):
            continue

        out: list[dict] = []
        seen: set[tuple] = set()
        merged: list[str] = []
        for cfg in configs:
            if not isinstance(cfg, dict):
                continue
            spec = _spec_key(cfg)
            if spec in seen:
                # 折疊後與前一筆規格相同＝本來就是同一個配置（先到先得，保留較早那筆的 id，
                # 讓指向較早 id 的 localStorage 綁定不受影響）
                merged.append(str(cfg.get("id") or "?"))
                continue
            seen.add(spec)
            provider, model, thinking, effort, temperature = spec
            out.append(
                {
                    "id": cfg.get("id"),
                    "provider": provider,
                    "model": model,
                    "thinking": thinking,
                    "reasoning_effort": effort,
                    "temperature": temperature,
                }
            )

        if out != configs:
            payload["llm_model_configs"] = out
            _save(conn, key, payload)
        if merged:
            # 被合併掉的 id 可能還被某人的 localStorage 指著；`useLlmAreaConfig` 的三級回落會接住
            # （該功能區回到預設起點，可見、非靜默），故不另建別名表。
            _log.warning(
                "settings[%s]：折疊惰性 temperature 後規格重複，合併 %d 筆 id=%s",
                key,
                len(merged),
                merged,
            )


def downgrade() -> None:
    """no-op：折掉的溫度值與合併掉的列都無從還原。需回退請用 pg_dump 備份。"""
    _log.warning("d1f7a3c8e520 為單向遷移，downgrade 不做任何事。")

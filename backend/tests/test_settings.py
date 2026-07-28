"""settings.py 核心邏輯測試（A schema：連線層 + 功能區默認旋鈕層）。

覆蓋：effective_llm_dict 的 area/overrides 組裝分支、model_capabilities_for provider 級能力、
舊多套 config 結構 → 新連線+功能區默認結構的一次性遷移、save_settings 機密不覆蓋既有語意。
"""

from __future__ import annotations

from app.core import settings as app_settings


# ── effective_llm_dict：area 預設 ──────────────────────────────────────────────────────────
def test_effective_llm_dict_uses_area_default():
    """area 有默認旋鈕 → 依該區默認組出 flat dict（連線反查對應 provider）。"""
    s = {
        "llm_connections": {"openai": {"base_url": "https://api.openai.com/v1"}},
        "llm_tokens": {"openai": "sk-live"},
        "llm_area_defaults": {
            "prejudge": {
                "provider": "openai",
                "model": "gpt-5.4-mini",
                "thinking": "on",
                "reasoning_effort": "high",
                "temperature": None,
            }
        },
    }
    eff = app_settings.effective_llm_dict(s, area="prejudge")
    assert eff["provider"] == "openai"
    assert eff["model"] == "gpt-5.4-mini"
    assert eff["base_url"] == "https://api.openai.com/v1"
    assert eff["api_token"] == "sk-live"
    assert eff["thinking"] == "on"
    assert eff["reasoning_effort"] == "high"


def test_effective_llm_dict_falls_back_to_stub_when_area_empty():
    """查無 area 默認（未設或該區無資料）→ 回退 _DEFAULT_LLM（stub，無 token）。

    base_url 即使在完全未配置時也補上 openai 官方端點（2026-07-28 起 effective_llm_dict 保證非空）；
    stub 判定只看 token（client.is_stub → not has_key），不受此影響。
    """
    eff = app_settings.effective_llm_dict(app_settings._blank_settings(), area="sandbox")
    assert eff["provider"] == "openai"
    assert eff["api_token"] == ""
    assert eff["base_url"] == app_settings.default_base_url_for("openai")


def test_effective_llm_dict_none_area_falls_back_to_default_knobs_not_other_areas():
    """area 缺省（None）→ 旋鈕回退 _DEFAULT_LLM（不誤用任一已存功能區的默認旋鈕）。

    連線解析仍獨立於旋鈕來源：_DEFAULT_LLM 的 provider（openai）若剛好有連線，token 仍會解出——
    這是預期行為（連線查找只認「當下決定用哪個 provider」，不論該 provider 是從 area 默認或
    _DEFAULT_LLM 來的）。
    """
    s = {
        "llm_connections": {"openai": {"base_url": "https://api.openai.com/v1"}},
        "llm_tokens": {"openai": "sk-live"},
        "llm_area_defaults": {"prejudge": {"provider": "openai", "model": "gpt-5.4-mini"}},
    }
    eff = app_settings.effective_llm_dict(s)
    assert eff["model"] == app_settings._DEFAULT_LLM["model"]  # 不是 prejudge 的 gpt-5.4-mini
    assert eff["api_token"] == "sk-live"  # _DEFAULT_LLM provider=openai 剛好有連線 → 仍解出


# ── effective_llm_dict：overrides ──────────────────────────────────────────────────────────
def test_effective_llm_dict_overrides_apply_non_none_fields():
    """overrides 的 model/thinking/reasoning_effort 非 None 值覆寫 area 默認。"""
    s = {
        "llm_connections": {"openai": {"base_url": ""}},
        "llm_tokens": {"openai": "sk-live"},
        "llm_area_defaults": {
            "prompt_debug": {
                "provider": "openai",
                "model": "gpt-5-mini",
                "thinking": "off",
                "reasoning_effort": "medium",
                "temperature": 0.7,
            }
        },
    }
    eff = app_settings.effective_llm_dict(
        s, area="prompt_debug", overrides={"model": "gpt-5.4-mini", "thinking": "on"}
    )
    assert eff["model"] == "gpt-5.4-mini"
    assert eff["thinking"] == "on"
    assert eff["reasoning_effort"] == "medium"  # 未覆寫沿用 area 默認
    assert eff["temperature"] == 0.7  # 未在 overrides key 中 → 不動


def test_effective_llm_dict_temperature_none_override_clears_saved_value():
    """temperature 的 None 覆寫有明確語意（本次改用 API 預設），需能清掉已保存的數值。"""
    s = {
        "llm_connections": {"openai": {"base_url": ""}},
        "llm_tokens": {"openai": "sk-live"},
        "llm_area_defaults": {
            "prompt_debug": {
                "provider": "openai",
                "model": "gpt-5-mini",
                "thinking": "on",
                "reasoning_effort": "medium",
                "temperature": 0.7,
            }
        },
    }
    eff = app_settings.effective_llm_dict(
        s, area="prompt_debug", overrides={"model": "gpt-5.4-mini", "temperature": None}
    )
    assert eff["model"] == "gpt-5.4-mini"
    assert eff["temperature"] is None
    assert eff["thinking"] == "on"  # 不在 overrides 內的欄位不受影響


def test_effective_llm_dict_overrides_provider_switches_connection():
    """overrides.provider 可切換本次連線（非僅切旋鈕），token/base_url 隨新 provider 反查。"""
    s = {
        "llm_connections": {
            "openai": {"base_url": "https://api.openai.com/v1"},
            "gemini": {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai"},
        },
        "llm_tokens": {"openai": "sk-openai", "gemini": "sk-gemini"},
        "llm_area_defaults": {
            "sandbox": {"provider": "openai", "model": "gpt-5-mini", "thinking": "off"}
        },
    }
    eff = app_settings.effective_llm_dict(
        s, area="sandbox", overrides={"provider": "gemini", "model": "gemini-3.5-flash"}
    )
    assert eff["provider"] == "gemini"
    assert eff["api_token"] == "sk-gemini"
    assert eff["base_url"].startswith("https://generativelanguage")
    assert eff["model"] == "gemini-3.5-flash"


def test_effective_llm_dict_fills_provider_default_base_url_when_connection_blank():
    """連線沒填 base_url（或存過空字串）→ 補該 provider 官方端點，不得落回 OpenAI。

    回歸自 2026-07-28：空值下游被 `or "https://api.openai.com/v1"` 吃掉，Gemini/ByteDance
    拿自家 token 打 OpenAI 端點整批回 401，錯誤訊息卻指向 OpenAI。
    """
    s = {
        "llm_connections": {"gemini": {"base_url": ""}, "bytedance": {}},
        "llm_area_defaults": {
            "prejudge": {"provider": "gemini", "model": "gemini-3.5-flash"},
            "sandbox": {"provider": "bytedance", "model": "seed-2-0-lite-260228"},
        },
    }
    assert app_settings.effective_llm_dict(s, area="prejudge")["base_url"].startswith(
        "https://generativelanguage"
    )
    assert "bytepluses" in app_settings.effective_llm_dict(s, area="sandbox")["base_url"]


def test_default_base_url_for_matches_provider_id_for_roundtrip():
    """default_base_url_for 與 provider_id_for 互為反向；未知 id 回退 openai 端點。"""
    for pid in ("openai", "gemini", "bytedance"):
        assert app_settings.provider_id_for(app_settings.default_base_url_for(pid)) == pid
    assert app_settings.default_base_url_for("nope") == app_settings.default_base_url_for("openai")


# ── model_capabilities_for ─────────────────────────────────────────────────────────────────
def test_model_capabilities_openai_locks_temperature_when_thinking():
    cap = app_settings.model_capabilities_for("gpt-5.4-mini")
    assert cap["temperatureLockedWhenThinking"] is True
    assert cap["lockedTemperatureValue"] == 1


def test_model_capabilities_gemini_does_not_lock_temperature():
    cap = app_settings.model_capabilities_for("gemini-3.5-flash")
    assert cap["temperatureLockedWhenThinking"] is False


def test_model_capabilities_unknown_model_falls_back_to_openai_default():
    """自訂/未知 model（不在任何 provider defaultModels 內）→ 回退 openai 級預設。"""
    cap = app_settings.model_capabilities_for("some-custom-finetune")
    assert cap["temperatureLockedWhenThinking"] is True


# ── 舊多套 config 結構 → 新連線+功能區默認結構 遷移 ──────────────────────────────────────────
def test_migrate_configs_to_areas_llm(temp_db):
    """A schema 改造前的 llm_configs[]（active 優先）→ llm_connections + 三區同初值 area 默認。"""
    from app.core import db

    legacy = {
        "llm_configs": [
            {
                "id": "cfg-openai",
                "provider": "openai",
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-5-mini",
                "thinking": "off",
                "reasoning_effort": "medium",
                "temperature": None,
            },
            {
                "id": "cfg-gemini",
                "provider": "gemini",
                "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
                "model": "gemini-3.5-flash",
                "thinking": "on",
                "reasoning_effort": "high",
                "temperature": None,
            },
        ],
        "active_llm_config_id": "cfg-gemini",
        "llm_tokens": {"cfg-openai": "sk-openai", "cfg-gemini": "sk-gemini"},
    }
    db.save_settings_row(app_settings.GLOBAL_SETTINGS_KEY, legacy)

    loaded = app_settings.load_settings()
    assert loaded["llm_connections"]["openai"]["base_url"] == "https://api.openai.com/v1"
    assert loaded["llm_connections"]["gemini"]["base_url"].startswith("https://generativelanguage")
    assert loaded["llm_tokens"]["openai"] == "sk-openai"
    assert loaded["llm_tokens"]["gemini"] == "sk-gemini"
    # active（cfg-gemini）旋鈕成為三個功能區的初始默認
    for area in app_settings.LLM_AREAS:
        assert loaded["llm_area_defaults"][area]["provider"] == "gemini"
        assert loaded["llm_area_defaults"][area]["model"] == "gemini-3.5-flash"
        assert loaded["llm_area_defaults"][area]["reasoning_effort"] == "high"
    # 遷移後立即持久化為新 shape（重讀一次不再觸發遷移分支）
    raw_row = db.load_settings_row(app_settings.GLOBAL_SETTINGS_KEY)
    assert "llm_connections" in raw_row


def test_migrate_configs_to_areas_qc(temp_db):
    """A schema 改造前的 qc_configs[]（同 env 多套時 active 優先）→ qc_connections（keyed by env）。"""
    from app.core import db

    legacy = {
        "qc_configs": [
            {"id": "qc-a", "env": "production", "host": "a.example", "port": 5432, "user": "u1"},
            {"id": "qc-b", "env": "production", "host": "b.example", "port": 5432, "user": "u2"},
        ],
        "active_qc_config_id": "qc-b",
        "qc_passwords": {"qc-a": "pw-a", "qc-b": "pw-b"},
    }
    db.save_settings_row(app_settings.GLOBAL_SETTINGS_KEY, legacy)

    loaded = app_settings.load_settings()
    assert loaded["qc_connections"]["production"]["host"] == "b.example"  # active 優先
    assert loaded["qc_passwords"]["production"] == "pw-b"


# ── save_settings：機密空/遮罩不覆蓋既有 ──────────────────────────────────────────────────
def test_save_settings_blank_token_does_not_clear_existing(temp_db):
    app_settings.save_settings(
        {
            "llm_connections": {"openai": {"base_url": ""}},
            "llm_tokens": {"openai": "sk-real"},
        }
    )
    # 再次 save 帶空字串 token（表單未改動送出的常見情境）→ 不覆蓋既有真值
    app_settings.save_settings({"llm_tokens": {"openai": ""}})
    loaded = app_settings.load_settings()
    assert loaded["llm_tokens"]["openai"] == "sk-real"


def test_save_settings_area_default_roundtrip(temp_db):
    app_settings.save_settings(
        {
            "llm_area_defaults": {
                "prejudge": {
                    "provider": "openai",
                    "model": "gpt-5.4",
                    "thinking": "on",
                    "reasoning_effort": "xhigh",
                    "temperature": None,
                }
            }
        }
    )
    loaded = app_settings.load_settings()
    assert loaded["llm_area_defaults"]["prejudge"]["model"] == "gpt-5.4"
    # 未觸碰的其他 area 不受影響（維持空）
    assert "prompt_debug" not in loaded["llm_area_defaults"]


def test_sanitize_drops_orphan_tokens_for_removed_connection(temp_db):
    """save 時整包替換 llm_connections 不含某 provider → 該 provider 的孤立 token 一併清除。"""
    app_settings.save_settings(
        {
            "llm_connections": {"openai": {"base_url": ""}, "gemini": {"base_url": ""}},
            "llm_tokens": {"openai": "sk-a", "gemini": "sk-b"},
        }
    )
    app_settings.save_settings({"llm_connections": {"openai": {"base_url": ""}}})
    loaded = app_settings.load_settings()
    assert "gemini" not in loaded["llm_tokens"]
    assert loaded["llm_tokens"]["openai"] == "sk-a"


# ── 旋鈕值域 SSOT（API 契約與 llm_model.json 同源）──────────────────────────────────────────
def test_thinking_modes_ssot_matches_execution_layer_native_enum():
    """thinking 值域＝Ark 原生三態，與 client._reasoning_kwargs 實際認得的字面一致。

    回歸自 2026-07-28：API 層 `LlmOverridesIn.thinking` 曾寫死舊值域 Literal["default","on","off"]，
    與執行層只認 enabled/disabled/auto 不符 → ByteDance 跑批一律 422（前端送 'enabled' 被擋在
    進入執行層之前），且 on/off 即使放行在執行層也是死值（既不設 extra_body 也不算 disabled）。
    """
    assert set(app_settings.LLM_THINKING_MODES) == {"enabled", "disabled", "auto"}
    assert "on" not in app_settings.LLM_THINKING_MODES
    assert "off" not in app_settings.LLM_THINKING_MODES
    # "default"＝不覆寫的元值，不該混進供應商參數值域
    assert "default" not in app_settings.LLM_THINKING_MODES


def test_llm_overrides_accepts_native_thinking_and_rejects_stale_values():
    """API 契約收前端旋鈕實際送出的值；舊值域字面必須被擋下（避免無聲復活）。"""
    import pydantic
    import pytest

    from app.api.routers.v1.prejudge import LlmOverridesIn

    for mode in (*app_settings.LLM_THINKING_MODES, "default", None):
        assert LlmOverridesIn(thinking=mode).thinking == mode
    for stale in ("on", "off"):
        with pytest.raises(pydantic.ValidationError):
            LlmOverridesIn(thinking=stale)
    for effort in (*app_settings.LLM_REASONING_EFFORTS, "default", None):
        assert LlmOverridesIn(reasoning_effort=effort).reasoning_effort == effort
    with pytest.raises(pydantic.ValidationError):
        LlmOverridesIn(reasoning_effort="ultra")

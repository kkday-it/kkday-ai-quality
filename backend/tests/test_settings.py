"""settings.py 核心邏輯測試（A schema：連線層 + 功能區默認旋鈕層）。

覆蓋：effective_llm_dict 的 area/overrides 組裝分支、model_capabilities_for provider 級能力、
舊多套 config 結構 → 新連線+功能區默認結構的一次性遷移、save_settings 機密不覆蓋既有語意。
"""

from __future__ import annotations

from app.core import settings as app_settings


def _area(provider: str, **knobs) -> dict:
    """組一個功能區的默認：當前選定供應商 + 該家的旋鈕（新 per-provider 形狀）。

    `llm_area_defaults[area]` 自 2026-07-30 起為 `{provider, knobs: {provider_id: 旋鈕}}`，
    測試一律用本 helper 表達，避免各處手抄巢狀結構抄錯。
    """
    return {"provider": provider, "knobs": {provider: dict(knobs)}}


# ── effective_llm_dict：area 預設 ──────────────────────────────────────────────────────────
def test_effective_llm_dict_uses_area_default():
    """area 有默認旋鈕 → 依該區默認組出 flat dict（連線反查對應 provider）。"""
    s = {
        "llm_connections": {"openai": {"base_url": "https://api.openai.com/v1"}},
        "llm_tokens": {"openai": "sk-live"},
        "llm_area_defaults": {
            "prejudge": _area(
                "openai",
                model="gpt-5.4-mini",
                thinking="on",
                reasoning_effort="high",
                temperature=None,
            )
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
        "llm_area_defaults": {"prejudge": _area("openai", model="gpt-5.4-mini")},
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
            "prompt_debug": _area(
                "openai",
                model="gpt-5-mini",
                thinking="off",
                reasoning_effort="medium",
                temperature=0.7,
            )
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
            "prompt_debug": _area(
                "openai",
                model="gpt-5-mini",
                thinking="on",
                reasoning_effort="medium",
                temperature=0.7,
            )
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
        "llm_area_defaults": {"sandbox": _area("openai", model="gpt-5-mini", thinking="off")},
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
            "prejudge": _area("gemini", model="gemini-3.5-flash"),
            "sandbox": _area("bytedance", model="seed-2-0-lite-260228"),
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
    # active（cfg-gemini）旋鈕成為三個功能區的初始默認（新形狀：旋鈕歸在該供應商名下）
    for area in app_settings.LLM_AREAS:
        area_cfg = loaded["llm_area_defaults"][area]
        assert area_cfg["provider"] == "gemini"
        assert area_cfg["knobs"]["gemini"]["model"] == "gemini-3.5-flash"
        assert area_cfg["knobs"]["gemini"]["reasoning_effort"] == "high"
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
                "prejudge": _area(
                    "openai",
                    model="gpt-5.4",
                    thinking="enabled",
                    reasoning_effort="xhigh",
                    temperature=None,
                )
            }
        }
    )
    loaded = app_settings.load_settings()
    assert loaded["llm_area_defaults"]["prejudge"]["knobs"]["openai"]["model"] == "gpt-5.4"
    # 未觸碰的其他 area 不受影響（維持空）
    assert "prompt_debug" not in loaded["llm_area_defaults"]


def test_stale_thinking_in_stored_area_defaults_is_normalized_on_read(temp_db):
    """庫內殘留的舊值域 thinking，讀取端必須自癒成當前值域。

    回歸自 2026-07-30：2026-07-23 LlmKnobs 重寫 + 2026-07-28 API validator 收緊值域，兩次都
    只改 code、沒正規化**已落庫**的 `llm_area_defaults`，於是 'on' 一路存活到前端原樣回送
    overrides，被 `LlmOverridesIn` 擋下 → 歸因列表「初判分類」整條 422 起不動。
    """
    from app.core import db

    # 繞過 save_settings（寫入端已正規化）直接塞髒值，模擬升級前既有環境的庫內狀態
    db.save_settings_row(
        app_settings.GLOBAL_SETTINGS_KEY,
        {
            "llm_connections": {"openai": {"base_url": ""}},
            "llm_area_defaults": {
                "prejudge": _area("openai", model="gpt-5.4", thinking="on"),
                "sandbox": _area("bytedance", model="seed-2-0", thinking="off"),
            },
        },
    )
    loaded = app_settings.load_settings()
    assert loaded["llm_area_defaults"]["prejudge"]["knobs"]["openai"]["thinking"] == "enabled"
    assert loaded["llm_area_defaults"]["sandbox"]["knobs"]["bytedance"]["thinking"] == "disabled"

    # 正規化後的值必須能通過 API 入口契約（即 422 不再發生）
    from app.api.routers.v1.prejudge import LlmOverridesIn

    for area in ("prejudge", "sandbox"):
        area_cfg = loaded["llm_area_defaults"][area]
        knobs = area_cfg["knobs"][area_cfg["provider"]]
        assert LlmOverridesIn(**knobs).thinking == knobs["thinking"]


def test_stale_thinking_is_normalized_on_write(temp_db):
    """舊前端／腳本送來的舊值域不得進庫——寫入端同步正規化，庫內值域恆為當前 SSOT。"""
    app_settings.save_settings({"llm_area_defaults": {"prejudge": _area("openai", thinking="on")}})
    stored = app_settings.load_settings()["llm_area_defaults"]["prejudge"]
    assert stored["knobs"]["openai"]["thinking"] == "enabled"


def test_legacy_thinking_map_covers_exactly_the_retired_domain():
    """翻譯表值域自我一致：來源皆為已退役字面、目標皆為當前合法值。"""
    assert set(app_settings._LEGACY_THINKING_MODES) == {"on", "off"}
    for stale, canonical in app_settings._LEGACY_THINKING_MODES.items():
        assert stale not in app_settings.LLM_THINKING_MODES
        assert canonical in app_settings.LLM_THINKING_MODES


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


# ── provider 三級解析（顯式 > 由 model 反推 > 功能區默認）─────────────────────────────
def _two_provider_settings() -> dict:
    """openai / bytedance 兩家都配好連線與 token 的 settings，供反推測試分辨打到哪一家。"""
    return {
        "llm_connections": {
            "openai": {"base_url": "https://api.openai.com/v1"},
            "bytedance": {"base_url": "https://ark.cn-beijing.volces.com/api/v3"},
        },
        "llm_tokens": {"openai": "sk-OPENAI", "bytedance": "tok-ARK"},
        "llm_area_defaults": {"prejudge": _area("openai", model="gpt-5.4-mini")},
        "provider_models": {"gemini": ["my-custom-gemini-x"]},
    }


def test_provider_id_for_model_known():
    """內建 model 反推得到正確供應商（多模型跑批逐一解端點的地基）。"""
    assert app_settings.provider_id_for_model("gpt-5.4-mini") == "openai"
    assert app_settings.provider_id_for_model("seed-2-0-lite-260428") == "bytedance"


def test_provider_id_for_model_unknown_raises():
    """未登記 model 一律拋錯、**不猜**——猜錯的後果是拿 A 家 token 打 B 家端點且不報錯。"""
    import pytest

    with pytest.raises(ValueError, match="未登記的 model"):
        app_settings.provider_id_for_model("totally-unknown-zzz")


def test_effective_llm_dict_infers_provider_from_model():
    """只覆寫 model（不帶 provider）→ 由 model 反推供應商，連線與 token 一起換過去。

    這是缺陷⑤的回歸鎖：舊實作只認顯式 provider，於是 ByteDance 的 model 會配上 area 默認的
    OpenAI token 與端點送出去——不報錯、只是結果錯。
    """
    eff = app_settings.effective_llm_dict(
        _two_provider_settings(), area="prejudge", overrides={"model": "seed-2-0-lite-260428"}
    )
    assert eff["provider"] == "bytedance"
    assert eff["api_token"] == "tok-ARK"
    assert "volces" in eff["base_url"]


def test_effective_llm_dict_explicit_provider_wins_over_model_inference():
    """顯式 provider 優先於 model 反推——保住「overrides 也能切換供應商連線」的既有語義。"""
    eff = app_settings.effective_llm_dict(
        _two_provider_settings(),
        area="prejudge",
        overrides={"model": "seed-2-0-lite-260428", "provider": "openai"},
    )
    assert eff["provider"] == "openai"
    assert eff["api_token"] == "sk-OPENAI"


def test_effective_llm_dict_infers_provider_from_custom_provider_models():
    """自訂 model（存在 settings.provider_models）也要能反推，否則自訂清單一律反推失敗。"""
    eff = app_settings.effective_llm_dict(
        _two_provider_settings(), area="prejudge", overrides={"model": "my-custom-gemini-x"}
    )
    assert eff["provider"] == "gemini"


def test_effective_llm_dict_unknown_model_keeps_area_default_and_warns(caplog):
    """完全未知的 model 不拋錯（自訂名是合法用法），沿用功能區默認但**留下告警**。"""
    import logging

    with caplog.at_level(logging.WARNING, logger="app.core.settings"):
        eff = app_settings.effective_llm_dict(
            _two_provider_settings(), area="prejudge", overrides={"model": "totally-unknown-zzz"}
        )
    assert eff["provider"] == "openai"  # area 默認
    assert any("無法反推供應商" in r.getMessage() for r in caplog.records)


def test_effective_llm_dict_no_overrides_uses_area_provider():
    """沒有 overrides 時行為不變（反推只在「有覆寫 model」時介入）。"""
    eff = app_settings.effective_llm_dict(_two_provider_settings(), area="prejudge")
    assert eff["provider"] == "openai"
    assert eff["model"] == "gpt-5.4-mini"


# ── per-provider 旋鈕隔離（缺陷③ 的回歸鎖）────────────────────────────────────────────
def test_area_defaults_per_provider_isolation(temp_db):
    """存某供應商的旋鈕**不得**沖掉同區其他供應商的旋鈕。

    這是 2026-07-30 改造前的實際行為：`llm_area_defaults[area]` 只裝得下一組旋鈕，前端三個
    供應商 tab 互相覆蓋——使用者以為三家各存了一份，庫裡永遠只有最後存的那一份，切回去只拿
    得到出廠預設。
    """
    app_settings.save_settings(
        {
            "llm_area_defaults": {
                "prejudge": _area("openai", model="gpt-5.4-mini", reasoning_effort="high")
            }
        }
    )
    app_settings.save_settings(
        {
            "llm_area_defaults": {
                "prejudge": _area("bytedance", model="seed-2-0-lite-260428", thinking="enabled")
            }
        }
    )
    knobs = app_settings.load_settings()["llm_area_defaults"]["prejudge"]["knobs"]
    assert set(knobs) == {"openai", "bytedance"}, "存第二家把第一家沖掉了"
    assert knobs["openai"]["model"] == "gpt-5.4-mini"
    assert knobs["openai"]["reasoning_effort"] == "high"
    assert knobs["bytedance"]["model"] == "seed-2-0-lite-260428"


def test_save_settings_keeps_active_provider_when_patch_omits_it(temp_db):
    """只更新某家旋鈕、不帶 provider → 當前選定不變（不是「沒帶就重設」）。"""
    app_settings.save_settings(
        {"llm_area_defaults": {"prejudge": _area("bytedance", model="seed-x")}}
    )
    app_settings.save_settings(
        {"llm_area_defaults": {"prejudge": {"knobs": {"openai": {"model": "gpt-5.4-mini"}}}}}
    )
    area_cfg = app_settings.load_settings()["llm_area_defaults"]["prejudge"]
    assert area_cfg["provider"] == "bytedance"  # 未指定 → 保留原選定
    assert set(area_cfg["knobs"]) == {"bytedance", "openai"}


def test_switching_to_unsaved_provider_uses_that_providers_default_model(temp_db):
    """切到「還沒存過旋鈕」的供應商 → model 用**該家自己的**預設，不是全域（openai 系）預設。

    否則切到 ByteDance 會拿到 gpt-* 的 model 名送出去，必然失敗。
    """
    app_settings.save_settings(
        {
            "llm_connections": {"bytedance": {"base_url": ""}},
            "llm_area_defaults": {"prejudge": _area("openai", model="gpt-5.4-mini")},
        }
    )
    eff = app_settings.effective_llm_dict(
        app_settings.load_settings(), area="prejudge", overrides={"provider": "bytedance"}
    )
    assert eff["provider"] == "bytedance"
    assert eff["model"] == app_settings.default_model_for("bytedance")
    assert not eff["model"].startswith("gpt-")
